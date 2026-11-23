"""The place profile's data model: a profile holds sections, a section holds facts.

Plain-language summary
----------------------
A **place profile** is a short, plain description of one county or one census
tract, built for market research: who lives there, what the homes are like,
how many homes are being permitted, how close homes are to frequent transit,
and how good the data are.

Three small records carry it:

- :class:`Fact` is one number (or one short word) with everything needed to
  trust it: its label, its unit, the text shown to people, a comparison with a
  bigger area ("higher than the state"), the id of the data source it came
  from (an entry in ``src/visionhousing/data/sources.yaml``), the source's
  edition ("vintage"), the year the number describes, the geography it is
  measured at, and a margin of error or range where the source gives one.
- :class:`Section` is one topic (``people``, ``housing`` ...): a title, one
  plain sentence that sums it up, its facts, what it cannot tell you, and the
  sources it used. A section can also say "no data for this place yet" or
  "coming soon" instead of failing.
- :class:`Profile` is the place itself: id, name, level (county or tract),
  the state, and its sections in a fixed order.

Nothing here computes or types a number. The sections fill the records from
the state data pack; the renderers only format what is in them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "LEVELS",
    "STATUSES",
    "Comparison",
    "Fact",
    "Level",
    "Profile",
    "Section",
    "Status",
]

Level = Literal["county", "tract"]
LEVELS: tuple[str, ...] = ("county", "tract")

Status = Literal["ok", "no_data", "coming_soon"]
STATUSES: tuple[str, ...] = ("ok", "no_data", "coming_soon")
"""``ok``: the section has facts. ``no_data``: the section exists but the pack has no
data for this place. ``coming_soon``: the section is planned but not built yet."""


@dataclass(frozen=True)
class Comparison:
    """The same measure for a bigger area, and how the place compares, in words.

    Attributes:
        against: The bigger area's level: ``"state"`` (for a county) or
            ``"county"`` (for a tract).
        name: The bigger area's name, for example ``"Pennsylvania"``.
        value: The bigger area's value, in the fact's unit.
        text: That value formatted for people.
        words: Plain words such as ``"higher than the state"``.
    """

    against: str
    name: str
    value: float | None
    text: str
    words: str

    def to_dict(self) -> dict[str, Any]:
        """Return the comparison as plain JSON-ready data."""
        return {
            "against": self.against,
            "name": self.name,
            "value": self.value,
            "text": self.text,
            "words": self.words,
        }


@dataclass(frozen=True)
class Fact:
    """One number about a place, with its source, edition and geography.

    Attributes:
        id: Stable id, ``<section>.<name>``, for example ``"people.population"``.
        label: Plain label shown to people.
        value: The number (or a short word for a category); ``None`` when the
            source has no value for this place.
        unit: Unit of ``value`` (``persons``, ``dollars``, ``share`` ...).
        text: ``value`` formatted for people (``"1,234"``, ``"$65,000"``, ``"12%"``).
        source: Id of the source in ``sources.yaml``.
        vintage: The source's edition (for example "2020-2024 5-year estimates").
        year: The year or years the number describes (for example "2020-2024").
        measured_at: Geography the number is measured at: ``state``,
            ``county``, ``tract`` or ``block``.
        comparison: The bigger area's value and plain words, or ``None``.
        moe: 90% margin of error in ``unit``, where the source gives one.
        low: Low end of the range (value minus margin), or ``None``.
        high: High end of the range (value plus margin), or ``None``.
        range_text: The range formatted for people ("$61,000 to $69,000"), or "".
        note: How the number was worked out, when that is not obvious.
    """

    id: str
    label: str
    value: float | int | str | None
    unit: str
    text: str
    source: str
    vintage: str
    year: str
    measured_at: str
    comparison: Comparison | None = None
    moe: float | None = None
    low: float | None = None
    high: float | None = None
    range_text: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the fact as plain JSON-ready data."""
        return {
            "id": self.id,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "text": self.text,
            "source": self.source,
            "vintage": self.vintage,
            "year": self.year,
            "measured_at": self.measured_at,
            "comparison": None if self.comparison is None else self.comparison.to_dict(),
            "moe": self.moe,
            "low": self.low,
            "high": self.high,
            "range_text": self.range_text,
            "note": self.note,
        }


@dataclass(frozen=True)
class Section:
    """One topic of a profile.

    Attributes:
        key: Section key, for example ``"people"``.
        title: Plain title, for example ``"People"``.
        summary: One plain sentence that sums the section up.
        facts: The facts, in display order.
        limits: What the section cannot tell you, in plain words.
        sources: Ids of the sources used, in ``sources.yaml``.
        status: ``ok``, ``no_data`` or ``coming_soon`` (see :data:`STATUSES`).
    """

    key: str
    title: str
    summary: str
    facts: tuple[Fact, ...] = ()
    limits: str = ""
    sources: tuple[str, ...] = ()
    status: Status = "ok"

    @classmethod
    def no_data(cls, key: str, title: str, why: str = "") -> Section:
        """A section that exists but has no data for this place yet."""
        return cls(
            key=key,
            title=title,
            summary="No data for this place yet.",
            limits=why or "The state data pack has no data for this section and place.",
            status="no_data",
        )

    @classmethod
    def coming_soon(cls, key: str, title: str) -> Section:
        """A planned section that is not built yet."""
        return cls(
            key=key,
            title=title,
            summary="Coming soon: not available for this place yet.",
            limits="This section is planned. Its data source has not been added yet.",
            status="coming_soon",
        )

    def fact(self, fact_id: str) -> Fact:
        """Return the fact with this id (``KeyError`` when absent)."""
        for f in self.facts:
            if f.id == fact_id:
                return f
        raise KeyError(fact_id)

    def to_dict(self) -> dict[str, Any]:
        """Return the section as plain JSON-ready data."""
        return {
            "key": self.key,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "facts": [f.to_dict() for f in self.facts],
            "limits": self.limits,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class Profile:
    """A place profile: one county or tract and its sections.

    Attributes:
        geoid: County (5 digits) or tract (11 digits) code.
        name: Plain name, for example ``"Allegheny County"``.
        level: ``"county"`` or ``"tract"``.
        state: Two-letter state code.
        state_name: The state's name.
        county: The county code (the place itself for a county).
        county_name: The county's name.
        sections: The sections, in display order.
        synthetic: True for the synthetic test state (not real data).
        pack_hash: Fingerprint of the state data pack, when it has one.
        sources: Source id to ``{"name", "publisher", "vintage", "url"}`` for
            every source a fact cites (the footnotes).
    """

    geoid: str
    name: str
    level: Level
    state: str
    state_name: str
    county: str
    county_name: str
    sections: tuple[Section, ...]
    synthetic: bool = False
    pack_hash: str | None = None
    sources: dict[str, dict[str, str]] = field(default_factory=dict)

    def section(self, key: str) -> Section:
        """Return the section with this key (``KeyError`` when absent)."""
        for s in self.sections:
            if s.key == key:
                return s
        raise KeyError(key)

    def facts(self) -> list[Fact]:
        """Every fact of every section, in display order."""
        return [f for s in self.sections for f in s.facts]

    def to_dict(self) -> dict[str, Any]:
        """Return the profile as plain JSON-ready data."""
        return {
            "geoid": self.geoid,
            "name": self.name,
            "level": self.level,
            "state": self.state,
            "state_name": self.state_name,
            "county": self.county,
            "county_name": self.county_name,
            "synthetic": self.synthetic,
            "pack_hash": self.pack_hash,
            "sections": [s.to_dict() for s in self.sections],
            "sources": self.sources,
        }
