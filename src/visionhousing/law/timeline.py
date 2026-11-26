"""Policy implementation timeline: when a rule applies, where, to which homes, and how sure we are.

Plain-language summary
----------------------
A housing law does not switch on in one step. A bill is introduced
(``pending``), signed (``enacted``), takes effect on a date
(``legally_effective``), may be paused by a court (``stayed``), is written into
local rules or guidance (``locally_implemented``) and finally shows up in
permits people can actually get (``practically_usable``). It can also be
replaced by a later law, which ends it on a date.

This module keeps those steps as **timeline records**. Each record is one
status of one provision, for one jurisdiction (a state, a county or a
municipality, by code) or one mapped area inside it (a polygon), for one
housing type, over one period (``start`` to ``end``; ``end`` empty = still so).

Two kinds of date are kept apart, so an older run can be reproduced exactly:

- ``start`` / ``end``: when the status held in law ("took effect");
- ``verified_on`` / ``end_verified_on``: when a person confirmed it from the
  authoritative source ("verified on"). A run made *as of* an earlier date sees
  only what had been verified by then: a record verified later is invisible,
  and an end verified later is ignored. An ``end`` with no ``end_verified_on``
  is a scheduled sunset written in the provision itself, known from the
  record's ``verified_on``: a run as of a date before the sunset still sees
  the rule end on it.

Each record is also **one version of one provision** (the
``law_provision_version`` contract, :func:`provision_versions`): it can name
the section, whether its text is the ``final`` enacted text or a ``draft``,
and keeps four kinds of date apart: ``adopted_on`` (passed by the legislative
body), ``published_on`` (printed or posted), ``start``/``end`` (effective) and
``last_checked_on`` (the latest re-reading). Adoption never stands in for
effectiveness: a record's ``start`` is the effective date its own source
states, or empty. It also carries the ``higher_law_floor`` that applies (a
state rule local law cannot go below), a ``source_fingerprint`` (SHA-256 of
the text read) and an ``excerpt_locator`` (where in the source the operative
words are).

A record whose start date its own source does not state is marked
``start_unknown: true`` (its ``start`` stays empty; nothing is guessed). Such a
record counts only **on or after its ``verified_on`` date**: that is the
earliest day we know it held, because a person read it in force then. Before
that day it is not taken as in force, so it never makes a rule look older than
the evidence shows (:attr:`TimelineRecord.in_force_from`). A verified record
with no ``start`` must say ``start_unknown: true``, so every empty start is a
documented choice.

Only ``review: verified`` records count. A change found automatically (for
example a new action on a bill's page, :func:`bill_review_queue`) enters as
``review: unverified`` and changes nothing until a person checks it.

:func:`eligibility_status` answers the engine's question: for this geography,
this housing type and this date, which status applies? The most specific
record wins (mapped area, then municipality, then county, then state); among
equals, the latest ``start``; a ``start_unknown`` record ranks below every
dated record at its level (its ``verified_on`` only gates when it counts). A
pending or enacted-but-not-yet-effective rule, or a stayed one, leaves the
baseline unchanged (``in_force`` False). No record means ``unknown``
(``in_force`` None), never "no".

Records live in ``assumptions/law/timeline.yaml`` (and test fixtures in
``tests/fixtures/law/``); mapped areas are GeoJSON polygons named by
``area_id``. Points are tested against polygons with a plain ray-casting rule
(no GIS library needed).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "COURT_STATUSES",
    "HOUSING_TYPES",
    "IN_FORCE",
    "PROVISION_VERSION_COLUMNS",
    "STATUSES",
    "TIMELINE_PATH",
    "Eligibility",
    "TimelineRecord",
    "bill_review_queue",
    "eligibility",
    "eligibility_status",
    "load_areas",
    "load_timeline",
    "point_in_area",
    "provision_versions",
    "schedule_comparison",
]

ROOT = Path(__file__).resolve().parents[3]
TIMELINE_PATH = ROOT / "assumptions" / "law" / "timeline.yaml"

STATUSES = (
    "pending",
    "enacted",
    "legally_effective",
    "stayed",
    "locally_implemented",
    "practically_usable",
)
"""The steps of a rule's life, in order (``stayed`` is a pause, not a step forward)."""

IN_FORCE = ("legally_effective", "locally_implemented", "practically_usable")
"""Statuses under which the rule is law on the date."""

HOUSING_TYPES = ("all", "adu", "missing_middle", "multifamily", "tod", "parking", "fees")
COURT_STATUSES = ("none_known", "challenged", "stayed", "upheld", "struck", "unknown")
REVIEW = ("verified", "unverified")
VERSION_STATES = ("", "final", "draft")
"""``final``: the enacted text; ``draft``: any earlier or pending text; empty: not recorded."""


def _as_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


class TimelineRecord(BaseModel):
    """One status of one provision, for one place and housing type, over one period."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    record_id: str = Field(pattern=r"^[a-z0-9_]+$")
    provision_id: str = ""
    citation: str = Field(min_length=3)
    jurisdiction: str = Field(pattern=r"^([A-Z]{2}|\d{2}|\d{5}|\d{10})$")
    area_id: str = ""
    housing_type: str = "all"
    status: str
    effect: str = Field(default="allows", pattern=r"^(allows|restricts|none)$")
    start: date | None = None
    start_unknown: bool = False
    end: date | None = None
    verified_on: date | None = None
    end_verified_on: date | None = None
    review: str = "verified"
    supersedes: str = ""
    superseded_by: str = ""
    court_status: str = "none_known"
    enforcement_status: str = ""
    text_version: str = ""
    retrieved_on: date | None = None
    scope: str = ""
    exclusions: str = ""
    agency: str = ""
    implementation_evidence: str = ""
    sources: tuple[str, ...] = ()
    source_id: str = ""
    note: str = Field(default="", max_length=600)
    # law_provision_version fields (docs/modules/law.md, "Provision versions")
    section: str = ""
    version_state: str = ""
    adopted_on: date | None = None
    published_on: date | None = None
    last_checked_on: date | None = None
    higher_law_floor: str = ""
    source_fingerprint: str = Field(default="", pattern=r"^(|sha256:[0-9a-f]{64}( .{0,200})?)$")
    excerpt_locator: str = ""

    @field_validator(
        "start",
        "end",
        "verified_on",
        "end_verified_on",
        "retrieved_on",
        "adopted_on",
        "published_on",
        "last_checked_on",
        mode="before",
    )
    @classmethod
    def _dates(cls, v: Any) -> date | None:
        return _as_date(v)

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}; got {v!r}")
        return v

    @field_validator("housing_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in HOUSING_TYPES:
            raise ValueError(f"housing_type must be one of {', '.join(HOUSING_TYPES)}; got {v!r}")
        return v

    @field_validator("court_status")
    @classmethod
    def _court(cls, v: str) -> str:
        if v not in COURT_STATUSES:
            raise ValueError(f"court_status must be one of {', '.join(COURT_STATUSES)}; got {v!r}")
        return v

    @field_validator("review")
    @classmethod
    def _review(cls, v: str) -> str:
        if v not in REVIEW:
            raise ValueError(f"review must be verified or unverified; got {v!r}")
        return v

    @field_validator("version_state")
    @classmethod
    def _version_state(cls, v: str) -> str:
        if v not in VERSION_STATES:
            raise ValueError(f"version_state must be final, draft or empty; got {v!r}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> TimelineRecord:
        if self.start and self.end and self.end <= self.start:
            raise ValueError(f"{self.record_id}: end must be after start")
        if self.review == "verified" and (self.verified_on is None or not self.sources):
            raise ValueError(f"{self.record_id}: a verified record needs verified_on and sources")
        if self.end_verified_on is not None and self.end is None:
            raise ValueError(f"{self.record_id}: end_verified_on needs an end date")
        if self.start_unknown and self.start is not None:
            raise ValueError(f"{self.record_id}: start_unknown needs an empty start")
        if self.review == "verified" and self.start is None and not self.start_unknown:
            raise ValueError(
                f"{self.record_id}: a verified record with no start must say start_unknown: true"
            )
        if self.version_state == "draft" and self.status in IN_FORCE:
            raise ValueError(f"{self.record_id}: a draft text cannot be in force")
        if self.last_checked_on and self.verified_on and self.last_checked_on < self.verified_on:
            raise ValueError(f"{self.record_id}: last_checked_on is before verified_on")
        return self

    @property
    def in_force_from(self) -> date | None:
        """First day the record can apply: ``start``, or ``verified_on`` if the start is unknown.

        ``None`` only for an unverified record with no start (it applies to
        nothing anyway).
        """
        if self.start is not None:
            return self.start
        return self.verified_on if self.start_unknown else None


def load_timeline(path: Path = TIMELINE_PATH) -> list[TimelineRecord]:
    """Read and check a timeline YAML file (``records:`` list); ids must be unique."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    records = [TimelineRecord(**r) for r in data.get("records", [])]
    ids = [r.record_id for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("timeline record ids must be unique")
    known = set(ids)
    for r in records:
        for link in (r.supersedes, r.superseded_by):
            if link and link not in known:
                raise ValueError(f"{r.record_id} links to unknown record {link}")
    return records


def load_areas(path: Path) -> dict[str, dict[str, Any]]:
    """GeoJSON features by their ``area_id`` property (a FeatureCollection file)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = data.get("features", [data] if data.get("type") == "Feature" else [])
    return {str(f["properties"]["area_id"]): f for f in feats}


def _in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xc:
                inside = not inside
    return inside


def point_in_area(point: tuple[float, float], feature: Mapping[str, Any]) -> bool:
    """True when ``(lon, lat)`` lies inside a GeoJSON Polygon or MultiPolygon (holes respected)."""
    geom = feature.get("geometry", feature)
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    x, y = point
    for poly in polys:
        if _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


@dataclass(frozen=True)
class Eligibility:
    """The answer for one geography, housing type and date.

    Attributes:
        status: The status that applies, or ``unknown``.
        in_force: True when the rule is law on the date (and not stayed);
            False when it is pending, enacted but not yet effective, or
            stayed; ``None`` when unknown.
        effect: What the rule does when in force (``allows`` / ``restricts``).
        record_id: The record used (empty when unknown).
        level: ``area``, ``municipality``, ``county``, ``state`` or ``""``.
        verified_on: When that record was confirmed.
        sources: Its sources.
        court_status: Its court status.
        considered: Every record that matched place, type and date, best first.
    """

    status: str
    in_force: bool | None
    effect: str = ""
    record_id: str = ""
    level: str = ""
    verified_on: date | None = None
    sources: tuple[str, ...] = ()
    court_status: str = ""
    considered: tuple[str, ...] = field(default=())


def _level(r: TimelineRecord) -> tuple[int, str]:
    if r.area_id:
        return 4, "area"
    n = len(r.jurisdiction)
    return {10: (3, "municipality"), 5: (2, "county")}.get(n, (1, "state"))


def _covers(r: TimelineRecord, state: str, state_fips: str, geoid: str) -> bool:
    j = r.jurisdiction
    if j.isalpha():
        return j == state
    if len(j) == 2:
        return j == state_fips
    return bool(geoid) and geoid.startswith(j)


def _end_known_on(r: TimelineRecord) -> date:
    """When a record's end became known.

    ``end_verified_on`` when given (an end found later, for example a repeal).
    Without it the end is a **scheduled sunset** written in the provision
    itself, so it was known when the record was verified (``verified_on``):
    a run as of a date before the sunset still sees that the rule ends then.
    """
    if r.end_verified_on is not None:
        return r.end_verified_on
    return r.verified_on or r.end or date.min


def eligibility_status(
    records: Iterable[TimelineRecord],
    *,
    state: str,
    housing_type: str,
    on: date,
    geoid: str = "",
    point: tuple[float, float] | None = None,
    areas: Mapping[str, Mapping[str, Any]] | None = None,
    as_of: date | None = None,
    state_fips: str = "",
) -> Eligibility:
    """Which status applies to ``housing_type`` homes in a place on date ``on``.

    Args:
        records: Timeline records (unverified ones are ignored).
        state: Two-letter state code.
        housing_type: One of :data:`HOUSING_TYPES` (records for ``all`` match too).
        on: The date asked about (for example a year of the five-year window).
        geoid: County (5) or municipality (10 digits) code, when known.
        point: ``(lon, lat)`` inside the place, for records tied to a mapped area.
            Without it, an area record does not apply (its area is not known
            to contain the place).
        areas: ``area_id`` to GeoJSON feature (:func:`load_areas`).
        as_of: Knowledge date. Records verified after it are ignored, and an
            end verified after it is not applied, so older runs reproduce.
        state_fips: The state's two-digit code, for records written that way.
    """
    hits: list[tuple[tuple[int, date], TimelineRecord, str]] = []
    for r in records:
        if r.review != "verified":
            continue
        if as_of is not None and (r.verified_on is None or r.verified_on > as_of):
            continue
        if r.housing_type not in ("all", housing_type):
            continue
        if not _covers(r, state, state_fips, geoid):
            continue
        if r.area_id:
            feat = (areas or {}).get(r.area_id)
            if point is None or feat is None or not point_in_area(point, feat):
                continue
        end = r.end
        if end is not None and as_of is not None and _end_known_on(r) > as_of:
            end = None  # the end was not yet known on the knowledge date
        begin = r.in_force_from  # verified_on when the start is unknown
        if (begin is not None and on < begin) or (end is not None and on >= end):
            continue
        rank, level = _level(r)
        # verified_on gates a start_unknown record but does not date it: it ranks below
        # every dated record at its level, so a newer dated amendment always wins.
        hits.append(((rank, r.start or date.min), r, level))
    if not hits:
        return Eligibility(status="unknown", in_force=None)
    hits.sort(key=lambda h: (h[0], h[1].record_id), reverse=True)
    _, best, level = hits[0]
    stayed = best.status == "stayed" or best.court_status == "stayed"
    return Eligibility(
        status="stayed" if stayed else best.status,
        in_force=(best.status in IN_FORCE) and not stayed,
        effect=best.effect,
        record_id=best.record_id,
        level=level,
        verified_on=best.verified_on,
        sources=best.sources,
        court_status=best.court_status,
        considered=tuple(h[1].record_id for h in hits),
    )


@lru_cache(maxsize=8)
def _cached(path: str) -> tuple[TimelineRecord, ...]:
    return tuple(load_timeline(Path(path)))


def eligibility(
    state: str,
    housing_type: str,
    on: date,
    *,
    geoid: str = "",
    point: tuple[float, float] | None = None,
    areas: Mapping[str, Mapping[str, Any]] | None = None,
    as_of: date | None = None,
    state_fips: str = "",
    path: Path = TIMELINE_PATH,
) -> Eligibility:
    """:func:`eligibility_status` on the shipped timeline (``assumptions/law/timeline.yaml``).

    The call the engine makes: for a state, a housing type, a date and,
    when known, a county or municipality code (and a point for mapped areas),
    which status applies and whether the rule is in force. ``state_fips``
    defaults to the state's two-digit code, so records written with it
    (``jurisdiction: "42"``) apply too.
    """
    if not state_fips:
        from visionhousing.data.sources import STATE_FIPS

        state_fips = STATE_FIPS.get(state.upper(), "")
    return eligibility_status(
        _cached(str(path)),
        state=state,
        housing_type=housing_type,
        on=on,
        geoid=geoid,
        point=point,
        areas=areas,
        as_of=as_of,
        state_fips=state_fips,
    )


def bill_review_queue(
    records: Iterable[TimelineRecord],
    pages: Mapping[str, Any],
    *,
    checked_on: date,
) -> list[TimelineRecord]:
    """Unverified records for bill changes found on their pages since the last verified record.

    Args:
        records: The timeline.
        pages: Provision id to a :class:`visionhousing.data.sources.legislation.BillStatus`
            (``actions`` as ``(text, date)``; ``enacted`` true when signed).
        checked_on: The date the pages were read.

    Returns:
        One ``review: unverified`` record per provision whose page shows a dated
        action after its latest verified record (status ``enacted`` from the
        signing date when the page shows the Governor's approval, else
        ``pending`` from the new action's date). They change no eligibility
        until a person verifies them.
    """
    records = list(records)
    out = []
    for pid, page in sorted(pages.items()):
        mine = [r for r in records if r.provision_id == pid and r.review == "verified"]
        if not mine:
            continue
        latest = max(mine, key=lambda r: (r.verified_on or date.min, r.start or date.min))
        dated = [(t, d) for t, d in page.actions if d is not None]
        if not dated:
            continue
        signed = [
            d for t, d in dated if "approved by the governor" in t.lower() or "act no" in t.lower()
        ]
        last_text, last_date = dated[-1]
        seen = latest.verified_on or date.min
        if signed and latest.status == "pending":
            status, start, why = "enacted", signed[0], "page shows the Governor's approval"
        elif last_date > seen:
            status, start, why = latest.status, last_date, f"new action: {last_text[:120]}"
        else:
            continue
        out.append(
            latest.model_copy(
                update={
                    "record_id": f"{latest.record_id}_queue_{checked_on:%Y%m%d}",
                    "status": status,
                    "start": start,
                    "end": None,
                    "end_verified_on": None,
                    "verified_on": None,
                    "review": "unverified",
                    "retrieved_on": checked_on,
                    "supersedes": "",
                    "superseded_by": "",
                    "note": f"Auto-detected on {checked_on}: {why}. Not verified.",
                }
            )
        )
    return out


# --------------------------------------------------------------------------- provision versions

PROVISION_VERSION_COLUMNS = (
    "record_id",
    "provision_id",
    "jurisdiction",
    "citation",
    "section",
    "housing_type",
    "status",
    "version_state",
    "text_version",
    "source_url",
    "effective_start",
    "effective_start_unknown",
    "effective_end",
    "adopted_on",
    "published_on",
    "verified_on",
    "last_checked_on",
    "supersedes",
    "superseded_by",
    "court_status",
    "higher_law_floor",
    "source_fingerprint",
    "excerpt_locator",
    "review",
)
"""Columns of the ``law_provision_version`` table (one version of one operative provision)."""


def provision_versions(records: Iterable[TimelineRecord]) -> Any:
    """The ``law_provision_version`` table: one row per timeline record.

    The timeline record already is one version of one provision over one
    period, so the table is a view of the records, not a second store. The
    effective dates (``effective_start``/``effective_end``), adoption,
    publication and the check dates stay in separate columns; an empty
    ``effective_start`` with ``effective_start_unknown`` true means the source
    does not state it (adoption is never copied in). ``source_url`` is the
    record's first source. ``court_status`` carries a stay.
    """
    import pandas as pd

    rows = []
    for r in records:
        rows.append(
            {
                "record_id": r.record_id,
                "provision_id": r.provision_id or r.record_id,
                "jurisdiction": r.jurisdiction,
                "citation": r.citation,
                "section": r.section,
                "housing_type": r.housing_type,
                "status": r.status,
                "version_state": r.version_state,
                "text_version": r.text_version,
                "source_url": r.sources[0] if r.sources else "",
                "effective_start": r.start,
                "effective_start_unknown": r.start_unknown,
                "effective_end": r.end,
                "adopted_on": r.adopted_on,
                "published_on": r.published_on,
                "verified_on": r.verified_on,
                "last_checked_on": r.last_checked_on or r.retrieved_on or r.verified_on,
                "supersedes": r.supersedes,
                "superseded_by": r.superseded_by,
                "court_status": r.court_status,
                "higher_law_floor": r.higher_law_floor,
                "source_fingerprint": r.source_fingerprint,
                "excerpt_locator": r.excerpt_locator,
                "review": r.review,
            }
        )
    return pd.DataFrame(rows, columns=list(PROVISION_VERSION_COLUMNS))


@dataclass(frozen=True)
class ScheduleRow:
    """One date of a current-law schedule next to the "freeze today's rules" comparison.

    Attributes:
        on: The date asked about.
        current_law: Status under the current-law convention (enacted changes
            take effect on their own dates; used in both forecast arms).
        current_record: The record behind it.
        frozen: Status if today's rules held throughout (the separately
            named comparison).
        frozen_record: The record behind it.
        differs: True when the two use different records on this date.
    """

    on: date
    current_law: str
    current_record: str
    frozen: str
    frozen_record: str
    differs: bool


def schedule_comparison(
    records: Iterable[TimelineRecord],
    *,
    state: str,
    housing_type: str,
    dates: Iterable[date],
    freeze_on: date,
    as_of: date | None = None,
    geoid: str = "",
    state_fips: str = "",
) -> list[ScheduleRow]:
    """Current-law schedule versus "freeze today's rules", date by date.

    **Current law** (the convention of both forecast arms): every enacted,
    verified change applies from its own effective date, including changes that
    take effect after ``freeze_on`` (for example Vermont's 24 V.S.A. § 4412
    text from January 1, 2028). A pending bill stays hypothetical: it is never
    in force here. **Freeze today's rules** is a separate comparison: the rule
    in force on ``freeze_on`` holds on every date. ``differs`` marks the dates
    where the two disagree, so the difference can be shown.
    """
    records = list(records)
    kw = {"state": state, "housing_type": housing_type, "geoid": geoid, "state_fips": state_fips}
    frozen = eligibility_status(records, on=freeze_on, as_of=as_of, **kw)
    out = []
    for on in dates:
        cur = eligibility_status(records, on=on, as_of=as_of, **kw)
        out.append(
            ScheduleRow(
                on=on,
                current_law=cur.status,
                current_record=cur.record_id,
                frozen=frozen.status,
                frozen_record=frozen.record_id,
                differs=cur.record_id != frozen.record_id,
            )
        )
    return out
