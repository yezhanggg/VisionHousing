"""The transit status ladder as text a screen, a brief or the question box can show.

Plain-language summary
----------------------
The transit-oriented density (TOD) run carries a **status** (``features.transit_status``):
for the state and for every county it says whether a schedule feed was discovered, whether
its terms allow use, how many bus stops had their service measured, whether any stop
qualifies as major and whether the mid-rise prototype pencils. When fewer than half of the
state's bus stops were measured (``tod.status_min_measured_bus_share``), the unmeasured part
has no defensible bound and the run marks its statewide range as **not shown**
(``status.statewide.shown`` = false). Wyoming is the case today.

:func:`status_views` turns that status into a **view** for each reform that carries one,
written here on the server so that every screen says the same words:

- ``shown``: whether the statewide range and percent change may be shown at all;
- ``partial``: whether some bus stops were not measured (the range, when shown, is then
  labelled ``label``, the completeness label);
- ``measured``: when the range is hidden, the measured contribution (the middle of the
  measured part's draws, the engine's own shown text) and the words to show it with;
- ``text``: one sentence for the question box and the hearing brief;
- ``package_note``: when the run holds the package, the note that the package total counts
  this reform by its measured contribution only;
- ``stages``, ``values`` and ``legend``: the ladder's questions and the word and display
  kind of every answer ("unknown" = not measured, never shown like "measured: none");
- ``state`` and ``counties``: one ladder row each (cells in stage order, completeness label).

The run reply (``POST /run``) carries these under ``status_views``; a reply saved without
them (an older static export) is read with :func:`status_views` again by whoever needs it.
Nothing here computes a number: the shown values are the explanation's text, or the
engine's formatter when a run has no explanation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["KINDS", "STAGE_TEXT", "VALUE_TEXT", "VIEW_VERSION", "status_views", "view_of"]

VIEW_VERSION = 1
"""Version of the view layout (bumped when a field changes meaning)."""

STAGE_TEXT: dict[str, tuple[str, str]] = {
    "discovered": ("Feed discovered", "Is a schedule feed listed for the agencies here?"),
    "legal": ("Use allowed", "May the discovered feeds be read and their counts used?"),
    "coverage": ("Service measured", "Were the bus stops' weekday departures counted?"),
    "headway": (
        "Frequent stop",
        "Does any stop meet the benchmark rule (rail, or 64+ weekday bus departures)?",
    ),
    "headway_peak": (
        "Frequent at peak",
        "Does any stop run every 15 minutes or better in both weekday peaks?",
    ),
    "feasible": ("Feasible", "Does the mid-rise prototype pencil on any eligible land?"),
}
"""Each stage's short label and its question (``features.transit_status.STAGES``)."""

VALUE_TEXT: dict[str, tuple[str, str]] = {
    "yes": ("Yes", "yes"),
    "partial": ("Partly", "partial"),
    "no": ("No", "no"),
    "unknown": ("Unknown (not measured)", "unknown"),
    "measured_zero": ("Measured: none", "measured_zero"),
    "not_reached": ("Not reached", "not_reached"),
}
"""Each answer's shown word and display kind."""

KINDS: tuple[tuple[str, str], ...] = (
    ("yes", "Yes: answered and true."),
    ("partial", "Partly: true for some agencies or stops, not all."),
    ("no", "No: answered and false."),
    ("unknown", "Unknown: not measured. It is not a zero."),
    ("measured_zero", "Measured: none. Every stop was measured and none qualifies."),
    ("not_reached", "Not reached: an earlier question has no yes."),
)
"""The legend: each display kind and what it means."""


def _obj(v: Any) -> dict[str, Any]:
    return dict(v) if isinstance(v, Mapping) else {}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _shown(reply: Mapping[str, Any], ref: str, record: Mapping[str, Any]) -> dict[str, str]:
    """The shown P10/P50/P90 of a record: the explanation's text, else the engine's formatter."""
    nums = _obj(_obj(reply.get("explanation")).get("numbers"))
    sh = _obj(_obj(nums.get(ref)).get("shown"))
    if all(k in sh for k in ("p10", "p50", "p90")):
        return {k: str(sh[k]) for k in ("p10", "p50", "p90")}
    from visionhousing.engine.records import format_number

    unit = str(record.get("unit") or "")
    out = {}
    for k in ("p10", "p50", "p90"):
        v = record.get(k)
        out[k] = "" if v is None else format_number(float(v), unit)
    return out


def _row(raw: Mapping[str, Any], stages: list[str]) -> dict[str, Any]:
    cells = []
    for s in stages:
        value = str(raw.get(s, "unknown"))
        word, kind = VALUE_TEXT.get(value, (value.replace("_", " "), "unknown"))
        cells.append({"stage": s, "value": value, "word": word, "kind": kind})
    measured = int(raw.get("bus_stops_measured") or 0)
    bus = int(raw.get("bus_stops") or 0)
    return {
        "geoid": str(raw.get("geoid", "")),
        "completeness": _cap(str(raw.get("completeness", ""))),
        "partial": measured < bus,
        "cells": cells,
    }


def view_of(reply: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    """The view of one reform in a run reply, or ``None`` when it carries no status."""
    outcome = _obj(_obj(reply.get("reform_results")).get(key))
    status = _obj(outcome.get("status"))
    if not status:
        return None
    stages = [str(s) for s in status.get("stages") or STAGE_TEXT]
    statewide = _obj(status.get("statewide"))
    shown = statewide.get("shown") is not False
    state = _obj(status.get("state"))
    label = _cap(str(statewide.get("label") or state.get("completeness") or ""))
    state_row = _row(state, stages)
    partial = state_row["partial"]
    title = str(outcome.get("title") or key)
    ref = f"reform_results.{key}.added"
    added = _shown(reply, ref, _obj(outcome.get("added")))
    share = float(statewide.get("min_measured_share") or 0.0)
    rule = (
        f"A statewide range is shown only when at least {share:.0%} of the state's bus stops "
        "have measured service (tod.status_min_measured_bus_share)."
    )
    measured = None
    package_note = None
    if shown:
        text = (
            f"{title}: a likely range of {added['p10']} to {added['p90']} added "
            f"(middle {added['p50']})" + (f"; {label[0].lower()}{label[1:]}." if partial else ".")
        )
        headline = ""
    else:
        measured = {
            "ref": ref,
            "label": "Measured contribution only",
            "p50": added["p50"],
            "text": f"Measured contribution only: middle {added['p50']}",
            "help": "Homes near stops whose service was measured; stops not measured add "
            "nothing here, which is not a zero.",
        }
        headline = f"Statewide range not shown. {label}."
        text = (
            f"{title}: the statewide range is not shown. {label}. "
            f"Measured contribution only: middle {added['p50']}. {rule} "
            "A feed that is only discovered adds no homes."
        )
        package = _obj(reply.get("package"))
        if key in (package.get("reforms") or []):
            package_note = (
                f"The package total counts {title[0].lower()}{title[1:]} by its measured "
                f"contribution only ({label[0].lower()}{label[1:]}); its statewide range is "
                "not shown."
            )
    return {
        "version": VIEW_VERSION,
        "reform": key,
        "title": title,
        "shown": shown,
        "partial": partial,
        "label": label,
        "badge": "Partial footprint" if partial else "Complete footprint",
        "hidden_word": "Statewide range not shown",
        "not_shown": "Not shown",
        "headline": headline,
        "measured": measured,
        "text": text,
        "rule": rule,
        "package_note": package_note,
        "stages": [
            {
                "key": s,
                "label": STAGE_TEXT.get(s, (s, ""))[0],
                "question": STAGE_TEXT.get(s, ("", s))[1],
            }
            for s in stages
        ],
        "legend": [{"kind": k, "word": VALUE_TEXT[k][0], "meaning": m} for k, m in KINDS],
        "state": state_row,
        "counties": {str(c): _row(r, stages) for c, r in _obj(status.get("counties")).items()},
        "ledger_ids": list(status.get("ledger_ids") or []),
        "source_ids": list(status.get("source_ids") or []),
    }


def status_views(reply: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{reform key: view}`` for every reform in a run reply that carries a status."""
    out: dict[str, dict[str, Any]] = {}
    for key in _obj(reply.get("reform_results")):
        view = view_of(reply, str(key))
        if view is not None:
            out[str(key)] = view
    return out
