"""The engine's link from the law layer to the missing-middle funnel: a dated state floor.

Plain-language summary
----------------------
Scenario modules never read the law files themselves; the engine does and hands
them what they need. For missing middle in a state with a statutory floor
(Vermont's 24 V.S.A. § 4412(1)(D)), :func:`middle_floor_for` builds the
year-by-year schedule of units already allowed
(:func:`visionhousing.law.rights.middle_floor_schedule`), reads the pack's
mapped sewer share per tract (``infra_tract.sewer_share``) and names the
sources behind both. The funnel
(:class:`visionhousing.scenarios.missing_middle.MiddleFloor`) then counts only
the building sizes above what the floor already allows.

Local readings (named variants only). Vermont and Wyoming town readings
(``assumptions/law/local_readings.yaml``) never reach a benchmark headline.
Two named variants read them, with settings in ``local_readings`` of
``assumptions/variants.yaml``:

- ``vt_missing_middle_town_bylaws`` (option ``local_rights: town_bylaws``):
  :func:`town_levels_for` turns each verified, in-force Vermont town's
  districts into the homes already allowed by right there
  (:func:`visionhousing.law.rights.state_floor` on each window year's date,
  the smallest over the listed districts; an unknown district leaves the town
  on the statewide floor), placed on tracts by the pack's
  ``tract_municipalities`` table (2020 housing units of each tract piece);
- ``vt_parking_town_bylaws`` and ``wy_parking_local`` (option ``minimums:
  local_readings``): :func:`local_parking_minimums` gives the verified
  parking minimums in the shape of
  :func:`visionhousing.law.parking_rules.verified_parking_minimums`, for the
  parking mix (:func:`visionhousing.scenarios.parking.minimum_mix`).

The same three rules hold in both: only facts marked ``verified`` (final bylaw
text, quote re-found) feed a number, never drafts, guidance or superseded
texts; a reading counts from its effective date (else ``in_force_by``, else
its review date, :func:`visionhousing.law.local_readings.counts_from`) and
only when that is on or before the run's as-of date; and what is not known
stays on the default. A town the pack cannot place on tracts (no
``tract_municipalities`` table) keeps the default too, and the run's notes say
so. For parking, a city that is a Census place but not a county subdivision
(Wyoming's Laramie, 7-digit ``geoid``) is placed through the pack's
``tract_places`` table instead (``engine.adapters._place_readings``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from visionhousing.ledger.loader import Ledger
from visionhousing.scenarios.missing_middle import YEAR_PARAMS, LocalLevel, MiddleFloor

__all__ = [
    "LOCAL_RIGHTS_CHOICES",
    "load_local_settings",
    "local_parking_minimums",
    "middle_floor_for",
    "town_levels_for",
    "town_tract_shares",
]

LOCAL_RIGHTS_CHOICES = ("none", "town_bylaws")
"""Missing-middle option ``local_rights``: the statewide floor only (the
benchmark) or, in addition, verified town bylaws (``vt_missing_middle_town_bylaws``)."""


def load_local_settings(path: Any = None) -> dict[str, Any]:
    """``local_readings`` of ``assumptions/variants.yaml``: the local-reading variants' settings."""
    from pathlib import Path

    from visionhousing.engine.variants import VARIANTS_PATH
    from visionhousing.ledger.loader import read_yaml

    data = read_yaml(Path(path) if path is not None else VARIANTS_PATH) or {}
    cfg = data.get("local_readings")
    if not isinstance(cfg, dict):
        raise KeyError("assumptions/variants.yaml has no local_readings block")
    return cfg


def _tract_munis(state: Any) -> pd.DataFrame | None:
    """The pack's ``tract_municipalities`` (shared with the parking adapter's reader)."""
    from visionhousing.engine.adapters import _law_table

    return _law_table(state, "tract_municipalities")


def town_tract_shares(
    tracts: pd.DataFrame, tract_munis: pd.DataFrame | None, geoid: str
) -> np.ndarray | None:
    """Per tract, the share of its 2020 housing units inside one town (10-digit county subdivision).

    ``geoid`` is state + county + county subdivision (``5002303175``, Barre
    city). A tract with no 2020 housing units splits evenly among its pieces
    (as :func:`visionhousing.scenarios.parking.minimum_mix` does). Returns
    ``None`` when there is no table, the geoid is not a county subdivision,
    or the town has no piece in the table (the town cannot be placed).
    """
    g = str(geoid)
    if tract_munis is None or len(g) != 10:
        return None
    tm = tract_munis.copy()
    tm["tract"] = tm["tract"].astype(str)
    tm["muni"] = tm["county"].astype(str).str.zfill(5) + tm["cousub"].astype(str).str.zfill(5)
    tm["hu"] = pd.to_numeric(tm["housing_units_2020"], errors="coerce").fillna(0.0)
    total = tm.groupby("tract")["hu"].transform("sum")
    size = tm.groupby("tract")["hu"].transform("size")
    tm["part"] = (tm["hu"] / total).where(total > 0, 1.0 / size)
    mine = tm[tm["muni"] == g].groupby("tract")["part"].sum()
    if mine.empty:
        return None
    ids = tracts["geoid"].astype(str)
    return ids.map(mine).fillna(0.0).to_numpy(dtype=float)


def _skip_reason(place: Any, on: date, require: Iterable[str]) -> str:
    """Why a place's reading does not count on ``on`` ("" when it counts)."""
    from visionhousing.law.local_readings import counts_from

    if place.status != "verified":
        return f"{place.status} reading"
    start = counts_from(place)
    if start is None or start > on:
        return f"counts from {start}, after the run's as-of date {on}"
    verified = {f.topic for f in place.facts if f.review == "verified"}
    missing = [t for t in require if t not in verified]
    if missing:
        return f"{', '.join(missing)} not verified"
    return ""


def town_levels_for(
    state: Any,
    sched: Any,
    *,
    as_of: date | str | None = None,
    readings: Any = None,
    records: Any = None,
    settings: Mapping[str, Any] | None = None,
) -> tuple[tuple[LocalLevel, ...], tuple[str, ...], tuple[str, ...]]:
    """Verified, in-force town bylaws on the reform's lots (``vt_missing_middle_town_bylaws``).

    For each place of the state in ``local_readings.yaml``: it counts when it
    is verified, counts from a date on or before ``as_of`` (the run's as-of
    date when ``None``) and its ``require_verified`` facts are verified. Its
    level per window year is the smallest ``max_units_by_right`` over its
    listed districts (:func:`visionhousing.law.rights.state_floor` on that
    year's date, the same provision as the schedule); one unknown district
    leaves the town on the statewide floor. The level is used only where it
    is above the floor (the funnel takes the larger), so a town that allows
    less than the floor changes nothing.

    Returns:
        ``(levels, source ids, notes)``. ``levels`` is empty when nothing counts.
    """
    from visionhousing.law.inforce import resolve_as_of
    from visionhousing.law.local_readings import district_inputs, load_local_readings, places_for
    from visionhousing.law.rights import state_floor
    from visionhousing.law.timeline import load_timeline

    cfg = dict(settings or load_local_settings())
    mm = dict(cfg.get("missing_middle") or {})
    code = str(sched.state).upper()
    if code not in {str(s).upper() for s in mm.get("states") or ()}:
        return (), (), (f"Town bylaw readings: none are used for {code}.",)
    on = resolve_as_of(as_of)
    recs = list(records) if records is not None else load_timeline()
    readings = readings or load_local_readings()
    table = _tract_munis(state)
    tracts = state.tracts
    used: list[LocalLevel] = []
    words: list[str] = []
    skipped: list[str] = []
    for place in places_for(code, readings):
        why = _skip_reason(place, on, mm.get("require_verified") or ())
        if not why and not place.districts:
            why = "no districts read"
        current: list[int] = []
        frozen = 0
        if not why:
            ds = district_inputs(place)
            for when in (*sched.dates, sched.freeze_on):
                vals = [
                    state_floor(d, when, records=recs, provision=sched.provision).max_units_by_right
                    for d in ds.values()
                ]
                if any(v is None for v in vals):
                    unknown = [c for c, v in zip(ds, vals, strict=True) if v is None]
                    why = f"units allowed not known in {', '.join(unknown)}"
                    break
                current.append(min(int(v) for v in vals if v is not None))
            if not why:
                frozen = current.pop()
        if not why:
            share = town_tract_shares(tracts, table, place.geoid)
            if share is None:
                why = (
                    "the pack has no tract_municipalities table to place it on tracts"
                    if table is None
                    else "not found in tract_municipalities"
                )
            else:
                used.append(LocalLevel(place.place_id, place.name, share, tuple(current), frozen))
                levels = " to ".join(str(x) for x in sorted(set(current)))
                words.append(f"{place.name} {levels} homes by right")
                continue
        skipped.append(f"{place.name} ({why})")
    src = (str(cfg["source_id"]), str(cfg["crosswalk_source_id"])) if used else ()
    notes = [
        f"Town bylaws (variant, readings in force by {on}): "
        + ("; ".join(words) if words else "none used")
        + "; there already allowed = max(state floor, town level). "
        "Lots split among towns by 2020 housing units (tract_municipalities); every other lot "
        "keeps the statewide floor.",
    ]
    if skipped:
        notes.append("Town readings not used: " + "; ".join(skipped) + ".")
    return tuple(used), src, tuple(notes)


def local_parking_minimums(
    code: str,
    *,
    as_of: date | str | None = None,
    readings: Any = None,
    settings: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Verified, in-force parking minimums from the local readings of one state.

    The rows have the columns of
    :func:`visionhousing.law.parking_rules.verified_parking_minimums`
    (``geoid``, ``county``, ``name``, ``min_apartment``, ``min_small``,
    ``doc_id``, ``section_cite``, ``effective_date``, ``reviewed_on``), so the
    parking mix reads them the same way. A place counts when it is verified
    and counts from a date on or before ``as_of``; only its ``parking`` fact
    marked ``verified`` is read, never a draft; a fact with an open flag keeps
    the default when ``skip_flagged``; the numbers are tried in the order of
    ``apartment_fields`` / ``small_fields``; a blank stays blank (default),
    never zero.

    Returns:
        ``(table, notes)``.
    """
    from visionhousing.law.inforce import resolve_as_of
    from visionhousing.law.local_readings import counts_from, load_local_readings, places_for
    from visionhousing.law.parking_rules import _COLUMNS

    cfg = dict(settings or load_local_settings())
    pk = dict(cfg.get("parking") or {})
    on = resolve_as_of(as_of)
    readings = readings or load_local_readings()
    no_apt = dict(pk.get("generic_not_for_apartments") or {})
    rows: list[dict[str, Any]] = []
    used: list[str] = []
    skipped: list[str] = []

    def first(nums: Mapping[str, Any], fields: Iterable[str]) -> float | None:
        for f in fields:
            x = nums.get(f)
            if x is not None:
                return float(x)
        return None

    for place in places_for(code, readings):
        why = _skip_reason(place, on, ())
        fact = next((f for f in place.facts if f.topic == "parking"), None)
        if not why and (fact is None or fact.review != "verified"):
            why = "parking " + ("not read" if fact is None else f"fact is {fact.review}")
        if not why and fact is not None and fact.flag and pk.get("skip_flagged", True):
            why = f"parking fact flagged: {fact.flag}"
        apt = small = None
        if not why and fact is not None:
            small = first(fact.numbers, pk.get("small_fields") or ())
            apt = (
                None
                if place.place_id in no_apt
                else first(fact.numbers, pk.get("apartment_fields") or ())
            )
            if apt is None and small is None:
                why = "no number in the parking fact"
        if why:
            skipped.append(f"{place.name} ({why})")
            continue
        if fact is None:  # pragma: no cover - a reason was set above
            continue
        if (apt is not None and apt < 0) or (small is not None and small < 0):
            raise ValueError(f"negative parking minimum for {place.place_id}")
        ev = fact.evidence[0]
        start = counts_from(place)
        rows.append(
            {
                "geoid": place.geoid,
                "county": place.geoid[:5],
                "name": place.name,
                "min_apartment": apt,
                "min_small": small,
                "doc_id": ev.doc_id,
                "section_cite": ev.locator,
                "effective_date": place.effective_date,
                "reviewed_on": place.reviewed_on.isoformat(),
            }
        )
        kept = "default" if apt is None else f"{apt:g}"
        used.append(
            f"{place.name} {small if small is None else f'{small:g}'} per home in 2-4 unit "
            f"buildings, {kept} per apartment (from {start})"
        )
    table = pd.DataFrame(rows, columns=list(_COLUMNS))
    notes = [
        f"Local parking readings ({code}, in force by {on}): "
        + ("; ".join(used) if used else "none used")
        + ". Every other place keeps the labelled default."
    ]
    if skipped:
        notes.append("Local parking readings not used: " + "; ".join(skipped) + ".")
    placed = set(table["geoid"])
    mine = {p.place_id: p.name for p in places_for(code, readings) if p.geoid in placed}
    for pid, why in no_apt.items():
        if pid in mine:
            words = " ".join(str(why).split())
            notes.append(f"{mine[pid]}: apartments keep the default ({words}).")
    return table, tuple(notes)


def middle_floor_for(
    state: Any,
    ledger: Ledger,
    method: str = "mix",
    *,
    local: str | None = None,
    as_of: date | str | None = None,
) -> tuple[MiddleFloor | None, tuple[str, ...]]:
    """The dated state floor for a run of ``state`` (a ``StateContext``), and notes.

    Reads ``missing_middle_application`` in ``assumptions/law/statutory_rights.yaml``
    (:func:`visionhousing.law.rights.middle_floor_schedule`) and the pack's
    ``infra_tract`` sewer share. Returns ``(None, ())`` for a state the block
    does not list. The floor applies to the mix method only; under another
    method a note says it was not applied. ``local="town_bylaws"`` (variant
    ``vt_missing_middle_town_bylaws``) adds the verified town bylaws
    (:func:`town_levels_for`, readings in force on ``as_of``).
    """
    choice = "none" if local in (None, "", "none") else str(local)
    if choice not in LOCAL_RIGHTS_CHOICES:
        raise ValueError(f"local_rights must be one of {LOCAL_RIGHTS_CHOICES}, got {local!r}")
    from visionhousing.law.rights import middle_floor_schedule
    from visionhousing.law.timeline import load_timeline
    from visionhousing.scenarios.extra_common import pack_table, table_sources

    code = str(getattr(state, "state", "") or "").upper()
    first = int(ledger[YEAR_PARAMS[0]].central)
    years = range(first, first + int(ledger[YEAR_PARAMS[1]].central))
    records = load_timeline()
    sched = middle_floor_schedule(code, years, records=records)
    if sched is None:
        return None, ()
    if method != "mix":
        return None, (
            f"State floor ({sched.provision}) not applied under the {method} method; "
            "it applies to the benchmark mix method only.",
        )
    tracts = state.tracts
    infra = pack_table(state, "infra_tract")
    sewer = np.zeros(len(tracts))
    notes = [
        f"State floor by date: {sched.why}. Only building sizes above what the floor already "
        "allows count, under current law in both arms (records floor_*).",
        "Where municipal water and sewer service is unknown the four-unit right is unknown; the "
        "served share is a likely range from the mapped sewer share and two draft judgement "
        "entries (no statewide municipal water map in the pack).",
    ]
    if infra is not None and "sewer_share" in infra.columns:
        share = infra.set_index("geoid")["sewer_share"]
        sewer = (
            tracts["geoid"].astype(str).map(share).astype(float).fillna(0.0).to_numpy(dtype=float)
        )
        srcs = set(table_sources(infra))
    else:
        srcs = set()
        notes.append("The pack has no sewer map (infra_tract): every lot counts as no record.")
    wanted = {*sched.versions, sched.frozen_version, sched.history_version}
    srcs |= {r.source_id for r in records if r.record_id in wanted and r.source_id}
    floor = MiddleFloor(sched, sewer, tuple(sorted(srcs)))
    if choice == "town_bylaws":
        levels, more, town_notes = town_levels_for(state, sched, as_of=as_of, records=records)
        floor = replace(floor, local=levels, source_ids=tuple(sorted(srcs | set(more))))
        notes += list(town_notes)
    return floor, tuple(notes)
