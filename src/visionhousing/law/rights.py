"""State floors on local zoning by date: Vermont's 24 V.S.A. § 4412 as a worked case.

Plain-language summary
----------------------
Some state laws set a *floor*: whatever a town's bylaw says, certain homes must
be allowed. Vermont's 24 V.S.A. § 4412(1)(D) makes a duplex a permitted use in
any district that allows year-round homes, and up to four units a permitted use
on a single-home lot where a district that allows homes is served by municipal
**water and sewer**. A second version of the same subdivision takes effect on
January 1, 2028 and adds one exception: the four-unit right does not apply in a
district that specifically requires multiunit buildings to have more than four
units. § 4412(1)(E) protects one accessory dwelling unit (ADU) with a
single-family home, except where a flood hazard or fluvial erosion bylaw
(§ 4424) applies.

This module answers, for one district on one date, what the state floor
guarantees. Which version applies comes from the dated timeline records
(:mod:`visionhousing.law.timeline`); the operative terms of each version
(unit counts, service conditions, the exception) are parameters in
``assumptions/law/statutory_rights.yaml``, not code.

Three rules keep the answer honest:

- **Unknown stays unknown.** If we do not know whether a district is served by
  water and sewer, or whether it requires more than four units, the answer is
  ``None`` (unknown), never "no right".
- **Count only the change.** :func:`legal_increment` compares a proposal with
  the rights a district already has. If a served district already allows four
  units and a bill allows six, only five and six units are new; if both allow
  the same designs, the pure legalization increment is zero.
- **Current law in both arms.** An enacted change with a future date (the 2028
  text) applies from its date in the baseline and the reform arm alike.
  "Freeze today's rules" is a separate, named comparison
  (:func:`freeze_difference`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from visionhousing.law.timeline import (
    TIMELINE_PATH,
    TimelineRecord,
    eligibility_status,
    load_timeline,
)

__all__ = [
    "RIGHTS_PATH",
    "SERVICE_CLASSES",
    "District",
    "FloorRights",
    "MiddleFloorSchedule",
    "freeze_difference",
    "legal_increment",
    "load_rights",
    "middle_floor_schedule",
    "state_floor",
]

ROOT = Path(__file__).resolve().parents[3]
RIGHTS_PATH = ROOT / "assumptions" / "law" / "statutory_rights.yaml"


@dataclass(frozen=True)
class District:
    """What is known about one zoning district (``None`` = not known).

    Attributes:
        year_round_residential: The district allows year-round residential use.
        allows_residential: The district allows residential use at all.
        municipal_water: Served by municipal water.
        municipal_sewer: Served by municipal sewer.
        required_min_units: The smallest number of units the district
            specifically requires in a multiunit structure (0 = no such
            requirement; ``None`` = not known).
        local_max_units: Most units the district's own bylaw allows by right
            on a single-home lot (``None`` = not read).
        flood_or_fluvial_bylaw: A § 4424 flood hazard or fluvial erosion bylaw
            applies to the lot.
    """

    year_round_residential: bool | None = None
    allows_residential: bool | None = None
    municipal_water: bool | None = None
    municipal_sewer: bool | None = None
    required_min_units: int | None = None
    local_max_units: int | None = None
    flood_or_fluvial_bylaw: bool | None = None


@dataclass(frozen=True)
class FloorRights:
    """What the state floor guarantees in one district on one date.

    Attributes:
        on: The date.
        version: Timeline record id of the version applied ("" when none).
        duplex: The duplex right applies (True), does not (False) or unknown (None).
        small_multiunit: The four-or-fewer-unit right applies, does not, or unknown.
        exception_applied: The 2028 exception removed the four-unit right.
        floor_units: Units the state floor guarantees (1 when neither right
            applies; ``None`` when unknown).
        max_units_by_right: The larger of the floor and the local bylaw's own
            by-right maximum (``None`` when either is unknown and could matter).
        adu_protected: One ADU with a single-family home is protected (True),
            left to the local § 4424 bylaw (False) or unknown (None).
        adu_version: Timeline record id of the ADU version applied.
        why: Plain words.
    """

    on: date
    version: str
    duplex: bool | None
    small_multiunit: bool | None
    exception_applied: bool
    floor_units: int | None
    max_units_by_right: int | None
    adu_protected: bool | None
    adu_version: str
    why: str


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def load_rights(path: Path = RIGHTS_PATH) -> dict[str, Any]:
    """``assumptions/law/statutory_rights.yaml``."""
    return _load(str(path))


def _all(values: Iterable[bool | None]) -> bool | None:
    """Three-valued AND: False if any is False, None if any is unknown, else True."""
    vals = list(values)
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def _version(
    records: list[TimelineRecord],
    rights: Mapping[str, Any],
    provision: str,
    on: date,
    as_of: date | None,
) -> tuple[str, dict[str, Any] | None]:
    spec = rights["provisions"][provision]
    e = eligibility_status(
        [r for r in records if r.provision_id == provision],
        state=spec["state"],
        housing_type=spec["housing_type"],
        on=on,
        as_of=as_of,
    )
    if not e.in_force:
        return "", None
    terms = spec["versions"].get(e.record_id)
    if terms is None:
        raise KeyError(f"{e.record_id} has no terms in statutory_rights.yaml ({provision})")
    return e.record_id, terms


def state_floor(
    district: District,
    on: date,
    *,
    records: Iterable[TimelineRecord] | None = None,
    rights: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    provision: str = "vt_24vsa_4412_1_d",
    adu_provision: str = "vt_24vsa_4412_1_e",
) -> FloorRights:
    """The Vermont § 4412 floor for one district on date ``on``.

    The version is the timeline record in force on ``on`` (so January 1, 2028
    switches to the text with the more-than-four-units exception). The
    **scope test** of that exception: it removes the four-unit right only when
    the district is known to require more than the version's threshold
    (``required_min_units`` > 4); unknown requirement gives an unknown right.
    """
    recs = list(records) if records is not None else load_timeline(TIMELINE_PATH)
    rights = rights or load_rights()
    vid, terms = _version(recs, rights, provision, on, as_of)
    why: list[str] = []
    if terms is None:
        why.append(f"no version of {provision} in force on {on}")
    duplex, small, exception, floor, best, more = _apply_terms(district, terms, vid)
    why += more
    adu_id, adu_terms = _version(recs, rights, adu_provision, on, as_of)
    if adu_terms is None:
        adu = None
    else:
        yields = adu_terms["adu"].get("yields_to") or []
        flood = district.flood_or_fluvial_bylaw
        adu = None if flood is None else not (flood and bool(yields))
        if yields and flood:
            why.append("ADU: a § 4424 flood or fluvial erosion bylaw applies; local rule decides")
    return FloorRights(
        on=on,
        version=vid,
        duplex=duplex,
        small_multiunit=small,
        exception_applied=exception,
        floor_units=floor,
        max_units_by_right=best,
        adu_protected=adu,
        adu_version=adu_id,
        why="; ".join(why),
    )


def _apply_terms(
    district: District, terms: Mapping[str, Any] | None, vid: str
) -> tuple[bool | None, bool | None, bool, int | None, int | None, list[str]]:
    """Duplex right, four-unit right, exception, floor units, best by right, and why."""
    why: list[str] = []
    duplex = small = None
    exception = False
    if terms is not None:
        dup = terms["duplex"]
        duplex = _all(getattr(district, k) for k in dup["requires"])
        sm = terms["small_multiunit"]
        small = _all(getattr(district, k) for k in sm["requires"])
        cut = sm.get("exception_min_units_above")
        if small and cut is not None:
            req = district.required_min_units
            if req is None:
                small = None
                why.append("unit requirement of the district not known")
            elif req > int(cut):
                small, exception = False, True
                why.append(f"district requires more than {cut} units: the four-unit right lapses")
        why.append(f"{vid}: duplex {duplex}, up to {sm['max_units']} units {small}")
    floor = _floor(terms, duplex, small)
    local = district.local_max_units
    if floor is not None and local is not None:
        best: int | None = max(floor, local)
    elif floor is None and local is not None and local >= _top(terms):
        best = local  # the local bylaw already allows at least what the floor could add
    else:
        best = None  # an unknown part could matter
    return duplex, small, exception, floor, best, why


def _top(terms: Mapping[str, Any] | None) -> int:
    if not terms:
        return 1
    return max(int(terms["duplex"]["units"]), int(terms["small_multiunit"]["max_units"]))


def _floor(terms: Mapping[str, Any] | None, duplex: bool | None, small: bool | None) -> int | None:
    """Units the floor guarantees: 4 with the small-multiunit right, 2 with a duplex, else 1."""
    if terms is None:
        return 1
    if small:
        return int(terms["small_multiunit"]["max_units"])
    if small is None and duplex is not False:
        return None  # the four-unit right may apply: unknown
    if duplex:
        return int(terms["duplex"]["units"])
    if duplex is None:
        return None
    return 1


def legal_increment(current_max: int | None, proposed_max: int) -> tuple[int, ...] | None:
    """Unit counts a proposal newly allows on a single-home lot.

    ``current_max`` is the most units already allowed by right (state floor or
    local bylaw, :attr:`FloorRights.max_units_by_right`). A district already
    allowing four with a proposal allowing six gains ``(5, 6)``; a proposal
    that allows no more than today gains ``()`` (a pure legalization increment
    of zero). Unknown current rights give ``None``.
    """
    if current_max is None:
        return None
    return tuple(range(int(current_max) + 1, int(proposed_max) + 1))


def freeze_difference(
    district: District,
    dates: Iterable[date],
    *,
    freeze_on: date | None = None,
    records: Iterable[TimelineRecord] | None = None,
    rights: Mapping[str, Any] | None = None,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Current law next to "freeze today's rules", date by date.

    Returns one dict per date: ``on``, ``current_law`` and ``frozen``
    (:class:`FloorRights`), and ``differs`` (the guaranteed units differ).
    ``freeze_on`` defaults to ``freeze_comparison.freeze_on`` in
    ``statutory_rights.yaml``.
    """
    recs = list(records) if records is not None else load_timeline(TIMELINE_PATH)
    rights = rights or load_rights()
    if freeze_on is None:
        raw = rights["freeze_comparison"]["freeze_on"]
        freeze_on = raw if isinstance(raw, date) else date.fromisoformat(str(raw))
    frozen = state_floor(district, freeze_on, records=recs, rights=rights, as_of=as_of)
    out = []
    for on in dates:
        cur = state_floor(district, on, records=recs, rights=rights, as_of=as_of)
        out.append(
            {
                "on": on,
                "current_law": cur,
                "frozen": frozen,
                "differs": (cur.version != frozen.version)
                and (cur.max_units_by_right != frozen.max_units_by_right),
            }
        )
    return out


# ---------------------------------------------------------------- missing middle


SERVICE_CLASSES = ("served", "unserved", "unknown")
"""Lots by municipal water and sewer service: both (``served``), not both
(``unserved``) and not known (``unknown``)."""


@dataclass(frozen=True)
class MiddleFloorSchedule:
    """The state floor a missing-middle reform is compared with, year by year.

    Built by :func:`middle_floor_schedule` from ``missing_middle_application``
    in ``statutory_rights.yaml``. For each service class, ``current[c][i]`` is
    the most units allowed by right in year ``years[i]`` under current law
    (enacted changes on their own dates, the same in both forecast arms),
    ``frozen[c][i]`` the same under "freeze today's rules" (the version in
    force on ``freeze_on`` held throughout; a separate comparison) and
    ``history[c]`` the rights already in force while the baseline's permit
    history was recorded. ``None`` means unknown.

    Attributes:
        state: Two-letter state code.
        provision: Provision id (``vt_24vsa_4412_1_d``).
        years: Window years.
        dates: The day each year's rights are read.
        versions: Timeline record id in force on each date (current law).
        freeze_on: The date "freeze today's rules" keeps.
        frozen_version: Timeline record id in force on ``freeze_on``.
        current: Service class to the units allowed by right per year.
        frozen: Service class to the units allowed by right per year if frozen.
        history: Service class to the units the history already reflects.
        history_version: Record id whose terms the history reflects.
        history_since: First day of those rights.
        service_params: ``water_share_in_sewer_area`` and
            ``served_share_no_sewer_record`` to their ledger ids.
        why: Plain words.
    """

    state: str
    provision: str
    years: tuple[int, ...]
    dates: tuple[date, ...]
    versions: tuple[str, ...]
    freeze_on: date
    frozen_version: str
    current: Mapping[str, tuple[int | None, ...]]
    frozen: Mapping[str, tuple[int | None, ...]]
    history: Mapping[str, int | None]
    history_version: str
    history_since: date
    service_params: Mapping[str, str]
    why: str

    def comparator(self, service: str, frozen: bool = False) -> tuple[int | None, ...]:
        """Units already allowed, per year, that the reform is compared with.

        The larger of the rights in force that year and the rights the
        baseline's history already reflects: production under rights the
        history already holds is in the baseline, so it is never counted again
        (no double count). ``None`` when either is unknown.
        """
        hist = self.history[service]
        rows = self.frozen[service] if frozen else self.current[service]
        return tuple(None if (m is None or hist is None) else max(m, hist) for m in rows)

    def newly_legal(
        self, units: int, service: str, frozen: bool = False
    ) -> tuple[bool | None, ...]:
        """Per year: a ``units``-home building is newly allowed by the reform (``None`` unknown)."""
        return tuple(None if m is None else units > m for m in self.comparator(service, frozen))


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def middle_floor_schedule(
    state: str,
    years: Iterable[int],
    *,
    records: Iterable[TimelineRecord] | None = None,
    rights: Mapping[str, Any] | None = None,
    as_of: date | None = None,
) -> MiddleFloorSchedule | None:
    """The § 4412(1)(D) schedule for the lots a missing-middle reform reaches.

    The reform reaches single-family lots in districts whose own bylaw allows
    one home (``missing_middle_application.district``). For each window year
    the floor is read with :func:`state_floor` on ``read_on`` of that year, for
    lots with municipal water and sewer, without, and with unknown service.
    Returns ``None`` for a state with no application block.
    """
    rights = rights or load_rights()
    app = rights.get("missing_middle_application") or {}
    if state.upper() not in {str(s).upper() for s in app.get("states", [])}:
        return None
    recs = list(records) if records is not None else load_timeline(TIMELINE_PATH)
    provision = str(app["provision"])
    month, day = (int(x) for x in str(app["read_on"]).split("-"))
    yrs = tuple(int(y) for y in years)
    dates = tuple(date(y, month, day) for y in yrs)
    freeze_on = _as_date(rights["freeze_comparison"]["freeze_on"])
    base = dict(app["district"])
    classes = app["service_classes"]
    districts = {c: District(**{**base, **(classes[c] or {})}) for c in SERVICE_CLASSES}

    def units(on: date, c: str) -> tuple[str, int | None]:
        f = state_floor(
            districts[c], on, records=recs, rights=rights, as_of=as_of, provision=provision
        )
        return f.version, f.max_units_by_right

    current = {c: tuple(units(d, c)[1] for d in dates) for c in SERVICE_CLASSES}
    versions = tuple(units(d, "served")[0] for d in dates)
    frozen_version, _ = units(freeze_on, "served")
    frozen = {c: (units(freeze_on, c)[1],) * len(dates) for c in SERVICE_CLASSES}
    hist = app["history"]
    hist_terms = rights["provisions"][provision]["versions"][hist["version"]]
    history = {
        c: _apply_terms(districts[c], hist_terms, str(hist["version"]))[4] for c in SERVICE_CLASSES
    }
    why = (
        f"{provision} read on {month:02d}-{day:02d} of each year {yrs[0]}-{yrs[-1]} "
        f"(versions {', '.join(dict.fromkeys(v or 'none' for v in versions))}); "
        f"history reflects {hist['version']} since {hist['since']}; "
        f"freeze keeps {frozen_version or 'none'} from {freeze_on}"
    )
    return MiddleFloorSchedule(
        state=state.upper(),
        provision=provision,
        years=yrs,
        dates=dates,
        versions=versions,
        freeze_on=freeze_on,
        frozen_version=frozen_version,
        current=current,
        frozen=frozen,
        history=history,
        history_version=str(hist["version"]),
        history_since=_as_date(hist["since"]),
        service_params=dict(app["service_share"]),
        why=why,
    )
