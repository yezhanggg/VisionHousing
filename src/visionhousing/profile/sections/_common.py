"""Shared helpers for section modules: make a fact the same way for every section.

Plain-language summary
----------------------
:class:`FactMaker` turns a measure (a function that works one number out for
an area) into a :class:`~visionhousing.profile.model.Fact`. It runs the same
function for the place and for the bigger area it is compared with, so the
two numbers always use one formula, then fills the formatted text, the plain
comparison words, the source id, the vintage and the margin of error.

A measure whose column is not in the pack raises
:class:`~visionhousing.profile.context.MissingDataError`; the maker then skips that
fact, so a pack with fewer columns (for example Testland) gives a shorter
section instead of an error.
"""

from __future__ import annotations

from collections.abc import Callable

from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.format import compare_words, fmt, is_missing
from visionhousing.profile.model import Chart, Comparison, Fact, Headline, Section

__all__ = ["MAX_POINTS", "FactMaker", "finish", "headline", "point", "sentence", "with_words"]

MAX_POINTS = 3
"""Most short summary points a section shows."""


class FactMaker:
    """Collects the facts of one section.

    Args:
        ctx: The profile context.
        key: The section key (fact ids start with it).
        place: The place.
        other: The bigger area it is compared with, or ``None``.
    """

    def __init__(self, ctx: ProfileContext, key: str, place: Area, other: Area | None):
        self.ctx, self.key, self.place, self.other = ctx, key, place, other
        self.facts: list[Fact] = []

    def add(
        self,
        name: str,
        label: str,
        unit: str,
        source: str,
        year: str,
        measure: Callable[[Area], Measure],
        *,
        compare: bool = True,
        note: str = "",
        measured_at: str | None = None,
    ) -> Fact | None:
        """Work out one fact; return it, or ``None`` when the pack lacks its data.

        Args:
            name: Short name; the fact id is ``<section>.<name>``.
            label: Plain label.
            unit: Unit of the value.
            source: Source id in ``sources.yaml``.
            year: Year or years the number describes.
            measure: Function from an area to a :class:`Measure`.
            compare: Also work the measure out for the bigger area.
            note: How the number was worked out (added to the measure's own note).
            measured_at: Geography, when it is not the place's level.
        """
        try:
            m = measure(self.place)
        except MissingDataError:
            return None
        comparison = None
        if compare and self.other is not None:
            try:
                o = measure(self.other)
            except MissingDataError:
                o = Measure(None)
            if not is_missing(o.value):
                comparison = Comparison(
                    against=self.other.level,
                    name=self.other.name,
                    value=o.value,
                    text=fmt(o.value, unit),
                    words=compare_words(m.value, o.value, unit, self.other.level),
                )
        value = None if is_missing(m.value) else m.value
        if isinstance(value, float) and unit in ("persons", "households", "units", "stops"):
            value = round(value)
        moe = None if is_missing(m.moe) or value is None else float(m.moe)  # type: ignore[arg-type]
        low = high = None
        if moe is not None and isinstance(value, int | float):
            low = max(float(value) - moe, 0.0)
            high = float(value) + moe
            if unit == "share":
                high = min(high, 1.0)
        fact = Fact(
            id=f"{self.key}.{name}",
            label=label,
            value=value,
            unit=unit,
            text=fmt(value, unit),
            source=source,
            vintage=self.ctx.vintage(source),
            year=year,
            measured_at=measured_at or self.place.level,
            comparison=comparison,
            moe=moe,
            low=low,
            high=high,
            range_text="" if low is None else f"{fmt(low, unit)} to {fmt(high, unit)}",
            note="; ".join(t for t in (m.note, note) if t),
        )
        self.facts.append(fact)
        return fact

    def get(self, name: str) -> Fact | None:
        """The fact added under ``name``, or ``None``."""
        for f in self.facts:
            if f.id == f"{self.key}.{name}":
                return f
        return None


def finish(
    key: str,
    title: str,
    summary: str,
    facts: list[Fact],
    limits: str,
    *,
    charts: list[Chart | None] | None = None,
    lead: Fact | None = None,
    points: list[str] | None = None,
) -> Section:
    """Build an ``ok`` section (or ``no_data`` when no fact has a value).

    Args:
        key: The section key.
        title: The section title.
        summary: One plain sentence.
        facts: The facts, in display order.
        limits: What the section cannot tell you.
        charts: Charts to attach; ``None`` entries (no data) are dropped.
        lead: The headline fact; default: the first fact with a value.
        points: Short summary points; empty ones are dropped and at most
            :data:`MAX_POINTS` are kept.
    """
    if not facts or all(f.value is None for f in facts):
        return Section.no_data(key, title)
    kept = tuple(c for c in (charts or []) if c is not None)
    sources: list[str] = []
    for src in (*(f.source for f in facts), *(s for c in kept for s in c.source_ids)):
        if src not in sources:
            sources.append(src)
    if lead is None or lead.value is None:
        lead = next((f for f in facts if f.value is not None), None)
    return Section(
        key, title, summary, tuple(facts), limits, tuple(sources), "ok",
        charts=kept,
        headline=headline(lead),
        summary_points=tuple(p for p in (points or []) if p)[:MAX_POINTS],
    )  # fmt: skip


def headline(fact: Fact | None) -> Headline | None:
    """A headline that repeats a fact (``None`` without a value)."""
    if fact is None or fact.value is None:
        return None
    words = fact.comparison.words if fact.comparison else ""
    return Headline(fact.id, fact.label, fact.text, words)


def point(fact: Fact | None, template: str) -> str:
    """Fill ``template`` with a fact's ``{text}`` and ``{words}``; "" without a value.

    ``{words}`` becomes `` (higher than the state)`` or nothing.
    """
    if fact is None or fact.value is None:
        return ""
    words = ""
    if fact.comparison and fact.comparison.words:
        words = f" ({fact.comparison.words})"
    return template.format(text=fact.text, words=words)


def with_words(fact: Fact | None) -> str:
    """``"$65,000 (higher than the state)"``, or the text alone without a comparison."""
    if fact is None:
        return ""
    if fact.comparison and fact.comparison.words and fact.value is not None:
        return f"{fact.text} ({fact.comparison.words})"
    return fact.text


def sentence(parts: list[str]) -> str:
    """Join clauses into one sentence: ``"a, b and c."`` with a capital first letter."""
    parts = [p for p in parts if p]
    if not parts:
        return ""
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return text[0].upper() + text[1:] + "."
