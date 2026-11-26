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

Three more records make the profile easier to scan:

- :class:`Chart` is a breakdown ready to draw (a pie, a bar chart, a stacked
  bar or a ranked bar list): its items with counts, **shares worked out here in
  Python** (the browser never divides), margins of error, the total, and the
  same shares for the bigger area to compare with.
- Each section also has a ``headline`` (its lead fact) and ``summary_points``
  (up to three short plain facts), above the full fact list.
- :class:`Rank` places a county among the state's counties ("12th highest of
  67") for a few market measures.

Nothing here computes or types a number. The sections fill the records from
the state data pack; the renderers only format what is in them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from visionhousing.profile.format import tidy

__all__ = [
    "CHART_KINDS",
    "LEVELS",
    "STATUSES",
    "Chart",
    "ChartCompare",
    "ChartItem",
    "Comparison",
    "Fact",
    "Headline",
    "Level",
    "Profile",
    "Rank",
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

    def to_dict(self, unit: str = "") -> dict[str, Any]:
        """Return the comparison as plain JSON-ready data (``value`` tidied by ``unit``)."""
        return {
            "against": self.against,
            "name": self.name,
            "value": tidy(self.value, unit),
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
        """Return the fact as plain JSON-ready data.

        Raw floats (``value``, ``moe``, ``low``, ``high`` and the comparison's value) are
        cut to 4 significant digits where that formats the same (see
        :func:`~visionhousing.profile.format.tidy`); ``text`` and ``range_text`` are made
        from the exact values and never change.
        """
        u = self.unit
        return {
            "id": self.id,
            "label": self.label,
            "value": tidy(self.value, u),
            "unit": self.unit,
            "text": self.text,
            "source": self.source,
            "vintage": self.vintage,
            "year": self.year,
            "measured_at": self.measured_at,
            "comparison": None if self.comparison is None else self.comparison.to_dict(u),
            "moe": tidy(self.moe, u),
            "low": tidy(self.low, u),
            "high": tidy(self.high, u),
            "range_text": self.range_text,
            "note": self.note,
        }


def _whole(x: float | None) -> float | int | None:
    """A whole-number float as an integer (shorter JSON); anything else unchanged."""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


ChartKind = Literal["pie", "bar", "stack", "rank"]
CHART_KINDS: tuple[str, ...] = ("pie", "bar", "stack", "rank")
"""``pie``: parts of a whole. ``bar``: ordered bands (age, income, year built).
``stack``: one stacked bar (rent burden). ``rank``: bars sorted largest first (sectors)."""


@dataclass(frozen=True)
class ChartItem:
    """One slice or bar of a chart.

    Attributes:
        label: Plain label, for example ``"25 to 34"``.
        value: The count (people, households, homes or jobs), or ``None``.
        share: ``value`` / the chart's total, rounded to 4 decimals, or ``None``.
        moe: 90% margin of error of ``value``, where the source gives one.
        key: Short stable key of the band, for example ``"25_34"``.
    """

    label: str
    value: float | None
    share: float | None
    moe: float | None = None
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the item as plain JSON-ready data (whole numbers as integers)."""
        return {
            "key": self.key,
            "label": self.label,
            "value": _whole(self.value),
            "share": self.share,
            "moe": _whole(self.moe),
        }

    def share_dict(self) -> dict[str, Any]:
        """Only the key and share: the form a comparison uses (labels match the chart's)."""
        return {"key": self.key, "share": self.share}


@dataclass(frozen=True)
class ChartCompare:
    """The same breakdown for the bigger area (the state for a county, the county for a tract).

    Its JSON keeps only each item's key and share (in the chart's order, with the chart's
    labels), to keep profiles small.
    """

    label: str
    items: tuple[ChartItem, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the comparison as plain JSON-ready data."""
        return {"label": self.label, "items": [i.share_dict() for i in self.items]}


@dataclass(frozen=True)
class Chart:
    """A breakdown ready to draw; every number in it is worked out in Python.

    Attributes:
        id: Stable id, ``<section>.<name>``, for example ``"people.age"``.
        kind: ``pie``, ``bar``, ``stack`` or ``rank`` (see :data:`CHART_KINDS`).
        title: Plain title.
        unit: What is counted (``persons``, ``households``, ``units``, ``jobs``).
        items: The slices or bars, in display order; their shares add up to 1
            (give or take rounding).
        total: The count the shares are taken of.
        compare: The bigger area's shares, or ``None``.
        source_ids: Ids of the sources in ``sources.yaml``.
        vintage: The source's edition.
        note: How the bands were made, in plain words.
    """

    id: str
    kind: ChartKind
    title: str
    unit: str
    items: tuple[ChartItem, ...]
    total: float | None
    compare: ChartCompare | None = None
    source_ids: tuple[str, ...] = ()
    vintage: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the chart as plain JSON-ready data."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "unit": self.unit,
            "items": [i.to_dict() for i in self.items],
            "total": _whole(self.total),
            "compare": None if self.compare is None else self.compare.to_dict(),
            "source_ids": list(self.source_ids),
            "vintage": self.vintage,
            "note": self.note,
        }


@dataclass(frozen=True)
class Headline:
    """A section's lead fact, shown large above the rest.

    Attributes:
        fact: The id of the fact it repeats.
        label: Plain label.
        text: The formatted value.
        words: How it compares with the bigger area, in words ("" when none).
    """

    fact: str
    label: str
    text: str
    words: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the headline as plain JSON-ready data."""
        return {"fact": self.fact, "label": self.label, "text": self.text, "words": self.words}


@dataclass(frozen=True)
class Rank:
    """Where a county stands among its state's counties on one measure.

    Attributes:
        id: Stable id, for example ``"rank.median_household_income"``.
        label: Plain label of the measure.
        value: The county's value, in ``unit``.
        text: ``value`` formatted for people.
        unit: Unit of ``value``.
        rank: 1 for the highest value; tied counties share the best rank among them.
        of: How many counties have a value.
        tied: True when another county has exactly the same (rounded) value.
        text_rank: For example ``"12th of 67"``.
        words: For example ``"12th highest of 67 counties in Pennsylvania"``.
        direction: ``"highest"`` or ``"lowest"``: which end of the list the words
            count from (the nearer one); ``rank`` always counts from the highest.
        source_ids: Ids of the sources in ``sources.yaml``.
        note: How the measure is worked out.
    """

    id: str
    label: str
    value: float | None
    text: str
    unit: str
    rank: int
    of: int
    tied: bool
    text_rank: str
    words: str
    direction: str = "highest"
    source_ids: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the rank as plain JSON-ready data (``value`` tidied, as for a fact)."""
        return {
            "id": self.id,
            "label": self.label,
            "value": tidy(self.value, self.unit),
            "text": self.text,
            "unit": self.unit,
            "rank": self.rank,
            "of": self.of,
            "tied": self.tied,
            "text_rank": self.text_rank,
            "words": self.words,
            "direction": self.direction,
            "source_ids": list(self.source_ids),
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
        charts: Breakdowns ready to draw (may be empty).
        headline: The lead fact, or ``None``.
        summary_points: Up to three short plain facts, for a quick read.
    """

    key: str
    title: str
    summary: str
    facts: tuple[Fact, ...] = ()
    limits: str = ""
    sources: tuple[str, ...] = ()
    status: Status = "ok"
    charts: tuple[Chart, ...] = ()
    headline: Headline | None = None
    summary_points: tuple[str, ...] = ()

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
            "headline": None if self.headline is None else self.headline.to_dict(),
            "summary_points": list(self.summary_points),
            "facts": [f.to_dict() for f in self.facts],
            "charts": [c.to_dict() for c in self.charts],
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
        ranks: For a county, its rank among the state's counties on a few
            measures (empty for a tract).
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
    ranks: tuple[Rank, ...] = ()

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
            "ranks": [r.to_dict() for r in self.ranks],
            "sources": self.sources,
        }
