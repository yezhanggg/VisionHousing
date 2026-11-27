"""Local zoning readings for states without a municipal law table (Vermont, Wyoming).

Plain-language summary
----------------------
Pennsylvania's municipal rules go into the ``muni_zoning_rules`` table, one row
per municipality in the state's planning record. Vermont and Wyoming have no
such record in their packs, so the readings made there are kept in
``assumptions/law/local_readings.yaml`` and read by this module. Nothing here
downloads anything.

The file has two lists:

- ``documents``: each text read (a bylaw, a use table, a guidance sheet), with
  its address, the date it was read, the SHA-256 of the file and of its text,
  and its own dates as printed. ``version_state`` says whether the text is the
  **final** law, a **superseded** one (it was law until ``valid_until``) or a
  **draft**. ``kind`` says whether it is the bylaw itself or the town's
  guidance (guidance is never the law).
- ``places``: one entry per town, city or county read, with ``facts`` (one per
  topic: the districts that allow homes year round, the duplex rule, small
  multi-unit buildings, the ADU rule, parking minimums, what the bylaw says
  about water and sewer service, the clause on unlisted uses) and, for
  Vermont, ``districts``: what is known about each district in the terms of
  :class:`visionhousing.law.rights.District` (``null`` = not known).

The rules the loader enforces:

1. **Every fact keeps its proof.** A fact marked ``verified`` (or
   ``superseded``) names the document, a locator (section, table, page) and a
   verbatim quote of at most 300 characters; its document has a SHA-256. A
   fact is ``verified`` only when its quote was found again at its locator in
   the saved copy, and only from final bylaw text: a fact from a draft or from
   guidance stays ``draft``, and one from a superseded text is ``superseded``.
2. **Unknown stays unknown.** A value nobody read is ``unknown`` (or ``null``
   in ``districts``), never "no". A town that could not be read is listed with
   ``status: not_read`` and the reason; it has no facts.
3. **Dates are kept apart.** Adoption, amendment and effective dates are the
   document's own, as printed (a year, a year and month, or a full date).
   Adoption never stands in for the effective date. With no effective date,
   a reading counts from ``in_force_by`` when known, otherwise from its
   ``reviewed_on`` date (:func:`counts_from`), the same rule as the
   municipal table (:mod:`visionhousing.law.inforce`).

What it gives the rest of the tool:

- :func:`load_local_readings` and :func:`places_for` (one state's places);
- :func:`district_inputs`: a place's districts as ``rights.District`` objects,
  so :func:`visionhousing.law.rights.state_floor` can say what Vermont's
  § 4412 guarantees there (unknown service gives an unknown four-unit right);
- :func:`readings_table`: one row per fact, for a table or a report;
- :func:`resident_coverage`: the share of a state's residents whose town has a
  verified, in-force reading, given a population table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from visionhousing.law.rights import District

__all__ = [
    "DOC_KINDS",
    "DOC_STATES",
    "FACT_REVIEWS",
    "PLACE_STATUSES",
    "READINGS_PATH",
    "TOPICS",
    "LocalDocument",
    "LocalFact",
    "LocalPlace",
    "LocalReadings",
    "counts_from",
    "district_inputs",
    "load_local_readings",
    "places_for",
    "readings_table",
    "resident_coverage",
]

_ROOT = Path(__file__).resolve().parents[3]
READINGS_PATH = _ROOT / "assumptions" / "law" / "local_readings.yaml"

DOC_STATES = ("final", "superseded", "draft")
"""Whether a text is the law in force, was the law until ``valid_until``, or is a draft."""
DOC_KINDS = ("bylaw", "guidance")
"""The law itself, or the jurisdiction's own guidance about it (never the law)."""
FACT_REVIEWS = ("verified", "draft", "superseded")
"""A fact's state: quote re-found in final bylaw text, anything less, or from a superseded text."""
PLACE_STATUSES = ("verified", "draft", "superseded", "not_read")
"""A place's state: some verified facts, drafts only, superseded text only, or nothing read."""
TOPICS = (
    "districts_residential",
    "duplex",
    "small_multiunit",
    "adu",
    "adu_owner_occupancy_text",
    "adu_size",
    "adu_occupancy",
    "parking",
    "service",
    "unlisted_uses",
    "adoption",
)
"""The topics a fact may answer."""

_PARTIAL = r"^(\d{4}(-\d{2}(-\d{2})?)?)?$"
_HEX = re.compile(r"^[0-9a-f]{64}$")
QUOTE_MAX = 300


def _date_text(v: Any) -> str:
    if v is None or v == "unknown":
        return ""
    return v.isoformat() if isinstance(v, date) else str(v)


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalDocument(_Row):
    """One text read: where it is, how it was fingerprinted, and its own dates."""

    doc_id: str = Field(pattern=r"^[a-z0-9_]+$")
    state: str = Field(pattern=r"^[A-Z]{2}$")
    title: str = Field(min_length=3)
    publisher: str
    url: str = Field(pattern=r"^https://\S+$")
    listed_on: str = ""
    retrieved_on: date
    sha256: str = ""
    text_sha256: str = ""
    text_chars: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=0)
    kind: str = "bylaw"
    version_state: str = "final"
    valid_until: date | None = None
    adopted_on: str = Field(default="", pattern=_PARTIAL)
    amended_through: str = Field(default="", pattern=_PARTIAL)
    effective_date: str = Field(default="", pattern=_PARTIAL)
    note: str = Field(default="", max_length=400)

    @field_validator("adopted_on", "amended_through", "effective_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> str:
        return _date_text(v)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in DOC_KINDS:
            raise ValueError(f"kind must be one of {DOC_KINDS}; got {v!r}")
        return v

    @field_validator("version_state")
    @classmethod
    def _state(cls, v: str) -> str:
        if v not in DOC_STATES:
            raise ValueError(f"version_state must be one of {DOC_STATES}; got {v!r}")
        return v

    @model_validator(mode="after")
    def _checks(self) -> LocalDocument:
        for name in ("sha256", "text_sha256"):
            value = getattr(self, name)
            if value and not _HEX.match(value):
                raise ValueError(f"{self.doc_id}: {name} must be 64 hex characters")
        if self.version_state == "superseded" and self.valid_until is None:
            raise ValueError(f"{self.doc_id}: a superseded text needs valid_until")
        return self


class Evidence(_Row):
    """Where a fact was read: the document, the locator and a verbatim quote."""

    doc_id: str
    locator: str = Field(min_length=2)
    quote: str = Field(min_length=3, max_length=QUOTE_MAX)
    offset: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)


class LocalFact(_Row):
    """One topic of one place: the value read (plain words), its proof and its review state."""

    topic: str
    value: str = Field(min_length=1, max_length=400)
    review: str = "draft"
    evidence: tuple[Evidence, ...] = ()
    check: str = Field(default="", max_length=300)
    flag: str = Field(default="", max_length=300)
    numbers: dict[str, float | None] = Field(default_factory=dict)

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        if v not in TOPICS:
            raise ValueError(f"topic must be one of {TOPICS}; got {v!r}")
        return v

    @field_validator("review")
    @classmethod
    def _review(cls, v: str) -> str:
        if v not in FACT_REVIEWS:
            raise ValueError(f"review must be one of {FACT_REVIEWS}; got {v!r}")
        return v

    @model_validator(mode="after")
    def _proof(self) -> LocalFact:
        if self.review in ("verified", "superseded") and not self.evidence:
            raise ValueError(
                f"a {self.review} {self.topic} fact needs evidence (doc, locator, quote)"
            )
        if self.review == "verified" and self.check:
            raise ValueError(f"{self.topic}: a fact with an open check stays draft")
        return self


_DISTRICT_FIELDS = set(District.__dataclass_fields__)


class LocalPlace(_Row):
    """One town, city or county read (or listed as not readable)."""

    place_id: str = Field(pattern=r"^[a-z]{2}_[a-z0-9_]+$")
    state: str = Field(pattern=r"^[A-Z]{2}$")
    name: str = Field(min_length=2)
    geoid: str = Field(default="", pattern=r"^(\d{5}|\d{7}|\d{10})?$")
    level: str = "municipality"
    status: str
    reviewed_on: date
    adopted_on: str = Field(default="", pattern=_PARTIAL)
    effective_date: str = Field(default="", pattern=_PARTIAL)
    in_force_by: str = Field(default="", pattern=_PARTIAL)
    facts: tuple[LocalFact, ...] = ()
    districts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    not_read_reason: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=600)

    @field_validator("adopted_on", "effective_date", "in_force_by", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> str:
        return _date_text(v)

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v not in ("municipality", "county"):
            raise ValueError(f"level must be municipality or county; got {v!r}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in PLACE_STATUSES:
            raise ValueError(f"status must be one of {PLACE_STATUSES}; got {v!r}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> LocalPlace:
        reviews = {f.review for f in self.facts}
        if self.status == "not_read":
            if self.facts or self.districts:
                raise ValueError(f"{self.place_id}: a place not read has no facts or districts")
            if not self.not_read_reason:
                raise ValueError(f"{self.place_id}: say why the place was not read")
        elif self.status == "verified" and "verified" not in reviews:
            raise ValueError(f"{self.place_id}: a verified place needs at least one verified fact")
        elif self.status == "superseded" and reviews - {"superseded"}:
            raise ValueError(f"{self.place_id}: a superseded place has only superseded facts")
        elif self.status == "draft" and "verified" in reviews:
            raise ValueError(f"{self.place_id}: a draft place has no verified facts")
        for code, fields in self.districts.items():
            extra = set(fields) - _DISTRICT_FIELDS
            if extra:
                raise ValueError(f"{self.place_id} {code}: not District fields: {sorted(extra)}")
        return self


class LocalReadings(_Row):
    """The whole file: documents and places, cross-checked."""

    documents: tuple[LocalDocument, ...]
    places: tuple[LocalPlace, ...]

    @model_validator(mode="after")
    def _links(self) -> LocalReadings:
        docs = {d.doc_id: d for d in self.documents}
        if len(docs) != len(self.documents):
            raise ValueError("duplicate doc_id in local_readings")
        ids = [p.place_id for p in self.places]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate place_id in local_readings")
        for p in self.places:
            for f in p.facts:
                for e in f.evidence:
                    d = docs.get(e.doc_id)
                    if d is None:
                        raise ValueError(f"{p.place_id} {f.topic}: unknown doc_id {e.doc_id}")
                    if d.state != p.state:
                        raise ValueError(f"{p.place_id}: {e.doc_id} is a {d.state} document")
                    if f.review in ("verified", "superseded") and not d.sha256:
                        raise ValueError(f"{p.place_id} {f.topic}: {e.doc_id} has no SHA-256")
                    if f.review == "verified" and (d.version_state != "final" or d.kind != "bylaw"):
                        raise ValueError(
                            f"{p.place_id} {f.topic}: a verified fact needs final bylaw text "
                            f"({e.doc_id} is {d.version_state} {d.kind})"
                        )
                    if f.review == "superseded" and d.version_state != "superseded":
                        raise ValueError(
                            f"{p.place_id} {f.topic}: {e.doc_id} is not a superseded text"
                        )
        return self


def load_local_readings(path: Path | str = READINGS_PATH) -> LocalReadings:
    """``assumptions/law/local_readings.yaml``, checked (raises ``ValueError`` on a broken row)."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return LocalReadings(
        documents=tuple(data.get("documents") or ()), places=tuple(data.get("places") or ())
    )


def places_for(state: str, readings: LocalReadings | None = None) -> list[LocalPlace]:
    """The places of one state, in file order."""
    readings = readings or load_local_readings()
    return [p for p in readings.places if p.state == state.upper()]


def district_inputs(place: LocalPlace) -> dict[str, District]:
    """A place's districts as ``rights.District`` objects; unknown stays ``None``."""
    return {code: District(**fields) for code, fields in place.districts.items()}


def _last_day(text: str) -> date | None:
    """The last day a partial date (year, year-month, full date) can mean; ``None`` when empty."""
    if not text:
        return None
    parts = [int(x) for x in text.split("-")]
    if len(parts) == 3:
        return date(*parts)
    if len(parts) == 2:
        y, m = parts
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1)
    return date(parts[0], 12, 31)


def counts_from(place: LocalPlace) -> date | None:
    """First day the reading counts as in force; ``None`` for a place with nothing verified.

    A known effective date decides (a year or year-month counts from its last
    possible day). Without one, a known ``in_force_by`` date; without either,
    the ``reviewed_on`` date, the first day a person saw the text posted as the
    law. The adoption date is never used.
    """
    if place.status != "verified":
        return None
    return _last_day(place.effective_date) or _last_day(place.in_force_by) or place.reviewed_on


READING_COLUMNS = (
    "state",
    "place_id",
    "name",
    "geoid",
    "level",
    "place_status",
    "topic",
    "value",
    "review",
    "doc_id",
    "locator",
    "offset",
    "page",
    "quote",
    "check",
    "flag",
    "reviewed_on",
    "counts_from",
)


def readings_table(state: str, readings: LocalReadings | None = None) -> pd.DataFrame:
    """One row per fact of one state (its first evidence item); empty with the columns if none."""
    rows: list[dict[str, Any]] = []
    for p in places_for(state, readings):
        start = counts_from(p)
        for f in p.facts:
            e = f.evidence[0] if f.evidence else None
            rows.append(
                {
                    "state": p.state,
                    "place_id": p.place_id,
                    "name": p.name,
                    "geoid": p.geoid,
                    "level": p.level,
                    "place_status": p.status,
                    "topic": f.topic,
                    "value": f.value,
                    "review": f.review,
                    "doc_id": e.doc_id if e else "",
                    "locator": e.locator if e else "",
                    "offset": e.offset if e else None,
                    "page": e.page if e else None,
                    "quote": e.quote if e else "",
                    "check": f.check,
                    "flag": f.flag,
                    "reviewed_on": p.reviewed_on.isoformat(),
                    "counts_from": start.isoformat() if start else "",
                }
            )
    return pd.DataFrame(rows, columns=list(READING_COLUMNS))


def resident_coverage(
    state: str,
    population: Mapping[str, float] | pd.Series,
    *,
    total: float,
    on: date,
    readings: LocalReadings | None = None,
    topics: Iterable[str] = (),
) -> dict[str, Any]:
    """The share of a state's residents whose place has a verified reading in force on ``on``.

    ``population`` maps a place's ``geoid`` to its residents and ``total`` is
    the state's. A place counts when it is verified, counts from ``on`` or
    earlier (:func:`counts_from`) and, when ``topics`` are given, has a
    verified fact on every one of them. Places without a population (no
    ``geoid``, or not in the table) are listed under ``missing`` and not
    counted. Returns the residents read, the share, and the place ids used.
    """
    want = set(topics)
    pop = dict(population)
    used: list[str] = []
    missing: list[str] = []
    read = 0.0
    for p in places_for(state, readings):
        start = counts_from(p)
        if start is None or start > on:
            continue
        verified = {f.topic for f in p.facts if f.review == "verified"}
        if want - verified:
            continue
        if not p.geoid or p.geoid not in pop:
            missing.append(p.place_id)
            continue
        read += float(pop[p.geoid])
        used.append(p.place_id)
    return {
        "state": state.upper(),
        "on": on.isoformat(),
        "residents_read": read,
        "residents_total": float(total),
        "share": read / total if total else None,
        "places": used,
        "missing": missing,
    }
