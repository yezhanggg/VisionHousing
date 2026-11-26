"""When a verified municipal rule counts as read: one in-force rule for the hooks and the tables.

Plain-language summary
----------------------
A rule read from an ordinance only matters once it is law. This module holds
the single test both :mod:`visionhousing.law.hooks` (what the engine uses) and
:mod:`visionhousing.law.coverage` (the coverage and ADU-category tables) apply,
so the two can never disagree about which rows count.

A row counts as **read** on the run's as-of date (``as_of`` in
``assumptions/outcome.yaml``, unless a date is passed in) when:

- its ``status`` is ``verified``; and
- its ``effective_date`` is on or before the as-of date. A year or a year-month
  counts from its last possible day (``2019`` means 2019-12-31), so a partial
  date is never taken as earlier than it could be; or
- it has no ``effective_date`` but a known ``in_force_by`` on or before the
  as-of date (the text stood in force by then); or
- it has neither, and a person saw it in force: it counts from its
  ``reviewed_on`` date, the first day a reading confirmed it as the posted
  law, never earlier (``unknown_effective: read_from_reviewed_on`` in
  ``assumptions/law/hooks.yaml``; the timeline's ``start_unknown`` rule). An
  ``adopted_on`` after the as-of date still rules it out.

**Adoption never stands in for effectiveness.** ``adopted_on`` (passed by the
governing body), the publication date and ``effective_date`` are kept apart:
an adoption date alone never puts a rule in force.

A read row **answers the ADU question** (``adu_read``) only when its
``adu_status`` is neither blank nor ``unknown`` (``adu_status_unknown:
treat_as_unread``), and, for ``not_addressed``, only when the ordinance's
clause on unlisted uses was read (``unlisted_uses``: prohibited, conditional
or special exception). "Not addressed" with no such clause is unknown, never
"prohibited". Such a row is unread for the ADU hook and the ADU categories,
never "not legal". Unknown is never treated as "no".

The pack's build-time flag ``future_effective`` is not used here: the dates
themselves are compared with the run's own as-of date, so a run frozen at an
earlier date reproduces.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

__all__ = [
    "OUTCOME_PATH",
    "adu_answered",
    "as_date",
    "hook_value",
    "in_force",
    "last_day",
    "read_flags",
    "resolve_as_of",
    "run_as_of",
]

OUTCOME_PATH = Path(__file__).resolve().parents[3] / "assumptions" / "outcome.yaml"

_BLANK = ("", "unknown", "nan", "none")


def hook_value(hooks: dict[str, Any] | None, name: str) -> Any:
    """A setting of ``assumptions/law/hooks.yaml``: from ``hooks`` if given there, else the file.

    There is no default in the code: a setting missing from both raises ``KeyError``.
    """
    if hooks and name in hooks:
        return hooks[name]
    from visionhousing.law.hooks import HOOKS_PATH

    data = yaml.safe_load(HOOKS_PATH.read_text(encoding="utf-8")) or {}
    if name not in data:
        raise KeyError(f"{name} is missing from {HOOKS_PATH.name} (assumptions/law)")
    return data[name]


def as_date(v: Any) -> date:
    """A ``date`` from a date or an ISO string."""
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def run_as_of(path: Path = OUTCOME_PATH) -> date:
    """The as-of date runs are frozen at (``as_of`` in ``assumptions/outcome.yaml``)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return as_date(data["as_of"])


def resolve_as_of(as_of: date | str | None) -> date:
    """``as_of`` as a date, or the run's as-of date when ``None``."""
    return run_as_of() if as_of is None else as_date(as_of)


def last_day(text: Any) -> date | None:
    """The last day a full or partial date can mean (``2019`` -> 2019-12-31); ``None`` if blank."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, date):
        return text
    t = str(text).strip()
    if not t or t.lower() in ("nan", "none"):
        return None
    parts = t.split("-")
    year = int(parts[0])
    if len(parts) == 1:
        return date(year, 12, 31)
    month = int(parts[1])
    if len(parts) == 2:
        return date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(1)
    return date.fromisoformat(t)


def in_force(row: Any, as_of: date, unknown_effective: str | None = None) -> bool:
    """True when a rule's dates put it in force on ``as_of`` (the verified test is separate).

    - ``effective_date`` known: in force once its last possible day is on or
      before ``as_of``. A known effective date always decides, so a rule taking
      effect later is not law yet, whatever ``in_force_by`` says;
    - ``effective_date`` unknown and ``in_force_by`` known and on or before
      ``as_of``: in force (the text as read stood in force by that date);
    - otherwise, with ``read_from_reviewed_on`` (the shipped rule,
      ``unknown_effective`` in ``assumptions/law/hooks.yaml``, read from there
      when not passed), in force from its ``reviewed_on`` date (a person saw
      it as the posted law then) unless its ``adopted_on`` is after ``as_of``;
      with any other setting (``unread``), never. Adoption alone never puts a
      rule in force.

    A row without date columns has every date unknown, so it is not in force.
    """
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    eff = last_day(get("effective_date"))
    if eff is not None:
        return eff <= as_of
    by = last_day(get("in_force_by"))
    if by is not None and by <= as_of:
        return True
    if unknown_effective is None:
        unknown_effective = str(hook_value(None, "unknown_effective"))
    if unknown_effective != "read_from_reviewed_on":
        return False
    seen = _day(get("reviewed_on"))
    if seen is None or seen > as_of:
        return False
    adopted = _first_day(get("adopted_on"))
    return adopted is None or adopted <= as_of


def _day(v: Any) -> date | None:
    """A full date from a date or ISO text; ``None`` when blank or partial."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, date):
        return v
    t = str(v).strip()[:10]
    return date.fromisoformat(t) if len(t) == 10 else None


def _first_day(text: Any) -> date | None:
    """The first day a full or partial date can mean (``2019`` -> 2019-01-01)."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, date):
        return text
    t = str(text).strip()
    if not t or t.lower() in ("nan", "none"):
        return None
    parts = [int(p) for p in t.split("-")]
    return date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)


def _text(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    t = str(v).strip().lower()
    return "" if t in ("nan", "none") else t


def adu_answered(status: Any, unlisted_uses: Any = None) -> bool:
    """True when an ``adu_status`` answers the ADU question.

    Blank or ``unknown`` does not. ``not_addressed`` answers it only when the
    ordinance's clause on unlisted uses was read (``unlisted_uses`` is
    ``prohibited``, ``conditional`` or ``special_exception``): an omitted use
    is not a prohibited one unless an exclusivity clause says so.
    """
    s = _text(status)
    if s in _BLANK:
        return False
    if s == "not_addressed":
        return _text(unlisted_uses) in ("prohibited", "conditional", "special_exception")
    return True


def read_flags(
    rows: pd.DataFrame,
    as_of: date | str | None = None,
    hooks: dict[str, Any] | None = None,
) -> tuple[pd.Series, pd.Series]:
    """``(read, adu_read)`` for every row of a rules table (or rules joined to municipalities).

    ``read``: ``status`` is ``verified`` and the row is in force on ``as_of``
    (:func:`in_force`). ``adu_read``: read, and ``adu_status`` answers the ADU
    question (:func:`adu_answered`). Rows with no rule (a left join) are unread.
    """
    when = resolve_as_of(as_of)
    rule = str(hook_value(hooks, "unknown_effective"))
    if "status" in rows.columns:
        verified = rows["status"].eq("verified")
    else:
        verified = pd.Series(False, index=rows.index)
    dated = [
        bool(v) and in_force(row, when, rule)
        for v, row in zip(verified, rows.to_dict("records"), strict=True)
    ]
    read = verified & pd.Series(dated, index=rows.index, dtype=bool)
    if "adu_status" in rows.columns:
        unlisted = rows["unlisted_uses"] if "unlisted_uses" in rows.columns else None
        known = pd.Series(
            [
                adu_answered(st, None if unlisted is None else unlisted.loc[i])
                for i, st in rows["adu_status"].items()
            ],
            index=rows.index,
            dtype=bool,
        )
    else:
        known = pd.Series(False, index=rows.index)
    return read, read & known
