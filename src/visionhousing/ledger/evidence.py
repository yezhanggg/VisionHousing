"""The evidence library: real reforms, what changed, and what was built afterwards.

``assumptions/evidence.yaml`` holds one entry per real-world reform or published
study that the model leans on. An entry says where and when a rule changed, what
the rule was, what was permitted before and after (with years and units), where
each figure was read, and how far the place is comparable to Pennsylvania.

The library has three uses, named in each entry's ``used_for`` list:

- ``calibration``: the figure helps set a range in the Assumption Ledger;
- ``hindcast``: the model can be run on the place and compared with what happened;
- ``evidence_card``: the figure is shown on a reform page as context.

Every figure carries the id of the source it was read from, and every source has
a web address, the date it was read, and an honest verification level:

- ``primary_document``: read in the publisher's own report, page or law text;
- ``primary_data``: computed here from the publisher's own data file;
- ``abstract_only``: read in the published abstract, not the full paper;
- ``secondary_summary``: read in another author's account of the original;
- ``excerpt_only``: seen only as a short excerpt; treat as unconfirmed.

Ledger parameters point at evidence with source ids that start with ``ev.``.
``unresolved_links`` lists any such id that has no entry here, so a test can
fail when the two files drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from visionhousing.ledger.loader import ROOT, Ledger, LedgerError, describe_errors, read_yaml

DEFAULT_EVIDENCE_PATH = ROOT / "assumptions" / "evidence.yaml"

EVIDENCE_PREFIX = "ev."
"""Ledger source ids that start with this point at an evidence entry."""

EVIDENCE_ID = re.compile(r"^ev\.[a-z_]+\.[a-z0-9_]+$")
"""Evidence ids look like ``ev.adu.seattle_2019``: prefix, reform, short name."""

Reform = Literal["adu", "missing_middle", "tod", "parking", "fees"]
"""The five benchmark reforms."""

EntryType = Literal["outcome", "cross_section", "rule"]
"""``outcome``: a before-and-after record for one reform. ``cross_section``: a
comparison across places or projects at one time. ``rule``: the text of a law or
definition the model uses, with no outcome."""

Use = Literal["calibration", "hindcast", "evidence_card"]
"""What the entry feeds (see the module docstring)."""

Verification = Literal[
    "primary_document", "primary_data", "abstract_only", "secondary_summary", "excerpt_only"
]
"""How the figures were checked, from strongest to weakest."""

VERIFICATION_ORDER: tuple[str, ...] = (
    "primary_document",
    "primary_data",
    "abstract_only",
    "secondary_summary",
    "excerpt_only",
)


class EvidenceSource(BaseModel):
    """One document or data file an entry's figures were read from.

    Attributes:
        id: Short name, unique within the entry; observations cite it.
        citation: Plain-text reference a reader can follow.
        url: Web address of the document or data file.
        accessed: Date it was read.
        locator: Where in the source the figures are (table, exhibit, page).
        verification: How the figures were checked (see ``Verification``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    url: str
    accessed: date
    locator: str = Field(min_length=1)
    verification: Verification

    @field_validator("url")
    @classmethod
    def _url_is_web_address(cls, url: str) -> str:
        if not url.startswith(("https://", "http://")):
            raise ValueError("url must start with https:// or http://")
        return url


class Observation(BaseModel):
    """One number read from a source.

    Attributes:
        measure: What was counted, in plain words.
        value: The number as published (or as computed, for ``primary_data``).
        unit: Its unit, for example ``permits per year`` or ``share``.
        period: The year or years it covers, for example ``2023`` or ``2016-2019``.
        source: Id of the entry source it was read from.
        note: Anything a reader needs to read the number correctly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    measure: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    period: str = Field(min_length=4)
    source: str = Field(min_length=1)
    note: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def _plain_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("must be a number, not true or false")
        return value


class EvidenceEntry(BaseModel):
    """One real-world reform or study.

    Attributes:
        place: Where (city, state, country, or a group of places).
        reform: Which benchmark reform it informs.
        entry_type: ``outcome``, ``cross_section`` or ``rule``.
        date_of_change: When the rule changed, as ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``.
        what_changed: The rule change, in plain words.
        before: Figures from before the change (``outcome`` entries).
        before_note: Why ``before`` is empty, when no earlier count exists.
        after: Figures from after the change (``outcome`` entries).
        findings: Figures for ``cross_section`` entries.
        rule_summary: The rule in plain words (required for ``rule`` entries).
        sources: Where every figure was read.
        verification: The weakest verification level among the sources.
        comparability: How far the place and its figures carry over to Pennsylvania.
        used_for: What the entry feeds.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    place: str = Field(min_length=1)
    reform: Reform
    entry_type: EntryType
    date_of_change: str
    what_changed: str = Field(min_length=1)
    before: tuple[Observation, ...] = ()
    before_note: str = ""
    after: tuple[Observation, ...] = ()
    findings: tuple[Observation, ...] = ()
    rule_summary: str = ""
    sources: tuple[EvidenceSource, ...] = Field(min_length=1)
    verification: Verification
    comparability: str = Field(min_length=1)
    used_for: tuple[Use, ...] = Field(min_length=1)

    @field_validator("date_of_change", mode="before")
    @classmethod
    def _date_text(cls, value: object) -> object:
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator("date_of_change")
    @classmethod
    def _date_shape(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
            raise ValueError("date_of_change must look like YYYY, YYYY-MM or YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def _parts_agree(self) -> EvidenceEntry:
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f"source ids repeat within the entry: {ids}")
        for obs in self.observations():
            if obs.source not in ids:
                raise ValueError(f"observation '{obs.measure}' cites unknown source '{obs.source}'")
        if self.entry_type == "outcome":
            if not self.after:
                raise ValueError("an outcome entry needs at least one 'after' figure")
            if not self.before and not self.before_note.strip():
                raise ValueError("an outcome entry needs 'before' figures or a 'before_note'")
        if self.entry_type == "cross_section" and not self.findings:
            raise ValueError("a cross_section entry needs at least one finding")
        if self.entry_type == "rule" and not self.rule_summary.strip():
            raise ValueError("a rule entry needs a 'rule_summary'")
        weakest = max(VERIFICATION_ORDER.index(s.verification) for s in self.sources)
        if VERIFICATION_ORDER.index(self.verification) < weakest:
            raise ValueError(
                f"verification '{self.verification}' is stronger than its weakest source "
                f"('{VERIFICATION_ORDER[weakest]}')"
            )
        if len(set(self.used_for)) != len(self.used_for):
            raise ValueError("used_for lists a use twice")
        return self

    def observations(self) -> Iterator[Observation]:
        """Yield every figure in the entry: before, after, then findings."""
        yield from self.before
        yield from self.after
        yield from self.findings


class EvidenceLibrary:
    """A checked, read-only set of evidence entries, by id.

    Args:
        entries: Evidence id to its checked entry.
        version: The file's format version.
    """

    def __init__(self, entries: Mapping[str, EvidenceEntry], version: int = 1):
        self._entries = dict(sorted(entries.items()))
        self.version = version

    def __getitem__(self, evidence_id: str) -> EvidenceEntry:
        try:
            return self._entries[evidence_id]
        except KeyError:
            raise LedgerError(f"evidence '{evidence_id}' is not in the library") from None

    def __contains__(self, evidence_id: object) -> bool:
        return evidence_id in self._entries

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def items(self) -> Iterator[tuple[str, EvidenceEntry]]:
        """Yield ``(id, entry)`` pairs in id order."""
        yield from self._entries.items()

    def by_reform(self, reform: str) -> list[str]:
        """Return the ids of entries for one reform."""
        return [eid for eid, e in self._entries.items() if e.reform == reform]

    def used_for(self, use: str) -> list[str]:
        """Return the ids of entries that list ``use``."""
        return [eid for eid, e in self._entries.items() if use in e.used_for]


def parse_evidence(data: Any, name: str = "evidence.yaml") -> EvidenceLibrary:
    """Check already-parsed evidence data and build an ``EvidenceLibrary``.

    Args:
        data: The parsed YAML: a mapping with ``version`` and ``entries``.
        name: File name used in error messages.

    Returns:
        The checked library.

    Raises:
        LedgerError: Something is wrong. Every bad entry is named.
    """
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        raise LedgerError(f"{name}: expected a top-level 'entries' mapping")
    unknown_top = sorted(
        k for k in data if k not in ("version", "entries") and not str(k).startswith("_")
    )
    if unknown_top:
        raise LedgerError(f"{name}: unknown top-level keys {unknown_top}")
    problems: list[str] = []
    entries: dict[str, EvidenceEntry] = {}
    for eid, raw in data["entries"].items():
        if not isinstance(eid, str) or not EVIDENCE_ID.match(eid):
            problems.append(f"evidence '{eid}': id must look like 'ev.reform.short_name'")
            continue
        if not isinstance(raw, dict):
            problems.append(f"evidence '{eid}': expected a mapping of fields")
            continue
        try:
            entry = EvidenceEntry.model_validate(raw)
        except ValidationError as exc:
            problems.append(f"evidence '{eid}': {describe_errors(exc)}")
            continue
        if eid.split(".")[1] != entry.reform:
            problems.append(f"evidence '{eid}': id names a reform other than '{entry.reform}'")
            continue
        entries[eid] = entry
    if problems:
        raise LedgerError(f"{name}: " + " | ".join(problems))
    if not entries:
        raise LedgerError(f"{name}: the 'entries' mapping is empty")
    return EvidenceLibrary(entries, version=int(data.get("version", 1)))


def load_evidence(path: Path | str | None = None) -> EvidenceLibrary:
    """Load and check the evidence file.

    Args:
        path: File to read. Defaults to ``assumptions/evidence.yaml``.

    Returns:
        The checked library.

    Raises:
        LedgerError: The file is missing, is not YAML, or has a bad entry.
    """
    path = Path(path) if path is not None else DEFAULT_EVIDENCE_PATH
    return parse_evidence(read_yaml(path), name=path.name)


def evidence_links(ledger: Ledger) -> dict[str, list[str]]:
    """Return, per parameter, the ledger source ids that point at evidence.

    Args:
        ledger: The checked ledger.

    Returns:
        Parameter id to its ``ev.`` source ids; parameters with none are left out.
    """
    links: dict[str, list[str]] = {}
    for pid, param in ledger.items():
        ids = [sid for sid in param.source_ids if sid.startswith(EVIDENCE_PREFIX)]
        if ids:
            links[pid] = ids
    return links


def unresolved_links(ledger: Ledger, library: EvidenceLibrary) -> list[str]:
    """List ledger references to evidence entries that do not exist.

    Args:
        ledger: The checked ledger.
        library: The checked evidence library.

    Returns:
        One ``"parameter -> source id"`` line per missing entry, sorted; empty if all resolve.
    """
    return sorted(
        f"{pid} -> {sid}"
        for pid, ids in evidence_links(ledger).items()
        for sid in ids
        if sid not in library
    )
