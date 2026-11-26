"""The comparison-record format: one checked entry per law, decision, program or study.

Plain-language summary
----------------------
A comparison record answers seven questions about a reform somewhere else:
what changed, where, when (enacted and effective dates), whether it is usable
yet (implementation status and court status), what outcome was measured, out
of what denominator and over what horizon, and how strong that evidence is.

Terms used here:

- **policy type**: one of twelve fixed kinds of reform (``POLICY_TYPES``), such
  as ``adu`` (accessory dwelling units) or ``water_compliance`` (proof of water
  supply before building). A record can carry several; the first is its main one.
- **implementation status**: where the rule is in its life, from ``pending``
  (a bill) through ``enacted`` (signed), ``legally_effective`` (in force),
  ``locally_implemented`` (cities have adopted it) to ``practically_usable``
  (projects are using it). ``stayed`` means a court paused it, ``struck_down``
  that a court voided it, ``lapsed`` that a bill died, ``not_law`` that the
  record is a plan, data program or study rather than a rule. The first six
  match the rule timeline kept with the law tables.
- **identification**: how a study separates the reform's effect from
  everything else. ``descriptive`` counts before and after; ``matched``
  compares with similar places that did not reform; ``causal`` uses a design
  built to isolate the effect (for example a difference-in-differences with
  checked pre-trends). Law records with no measured outcome use
  ``not_applicable``.
- **verified**: ``True`` only when the citation and every date were read on an
  official page (a legislature, court, agency or city site, or for a study the
  publisher's own page). Otherwise ``verification_note`` says what is missing.

Every check fails with a message that names the record, so a typo cannot slip
through silently.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "IDENTIFICATION",
    "IMPLEMENTATION_STATUSES",
    "LITIGATION_STATUSES",
    "POLICY_TYPES",
    "STATES",
    "ComparisonRecord",
    "DiscoveryEntry",
    "EffectiveDate",
    "Litigation",
    "ObservedResult",
    "SourceLink",
    "Tracker",
]

POLICY_TYPES: tuple[str, ...] = (
    "adu",
    "missing_middle",
    "tod",
    "parking",
    "fees",
    "commercial_land_housing",
    "adaptive_reuse",
    "starter_lots",
    "permit_timing",
    "infrastructure_finance",
    "water_compliance",
    "court_decision",
)
"""The twelve policy types, in the column order of the completeness matrix."""

IMPLEMENTATION_STATUSES: tuple[str, ...] = (
    "pending",
    "enacted",
    "legally_effective",
    "stayed",
    "locally_implemented",
    "practically_usable",
    "lapsed",
    "struck_down",
    "not_law",
    "unknown",
)
"""Where a rule is in its life; the first six match the law timeline's statuses."""

LITIGATION_STATUSES: tuple[str, ...] = (
    "none_known",
    "challenged",
    "stayed",
    "decided",
    "upheld",
    "struck",
    "not_applicable",
    "unknown",
)
"""Court status of a record (``decided`` when the court ruled on parts both ways)."""

IDENTIFICATION: tuple[str, ...] = ("not_applicable", "descriptive", "matched", "causal")
"""Evidence strength, weakest to strongest; ``not_applicable`` for law with no outcome."""

STATES: tuple[str, ...] = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE",
    "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
)  # fmt: skip
"""The 50 states and the District of Columbia (two-letter postal codes)."""

DISCOVERED_VIA = ("addendum", "nyu_furman", "aei", "open_states", "pew_2026", "search")
"""Where a candidate reform was first found. Trackers are for finding, never citing."""

ID_PATTERN = r"^[a-z]{2}\.[a-z0-9_.]+$"
URL_PATTERN = re.compile(r"^https?://[^\s/]+\.[^\s/]+\S*$")
# Code publishers whose pages are never fetched (their terms limit automated reading).
NEVER_FETCH = ("ecode360.com", "municode.com", "amlegal.com")

PolicyType = Literal[
    "adu",
    "missing_middle",
    "tod",
    "parking",
    "fees",
    "commercial_land_housing",
    "adaptive_reuse",
    "starter_lots",
    "permit_timing",
    "infrastructure_finance",
    "water_compliance",
    "court_decision",
]


def _check_url(value: str) -> str:
    if not URL_PATTERN.match(value):
        raise ValueError(f"not a web address: {value[:60]!r}")
    return value


Url = Annotated[str, AfterValidator(_check_url)]
"""A web address (http or https, no spaces)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceLink(_Strict):
    """One page the record was read from; ``official`` marks a government or publisher page."""

    label: str = Field(min_length=2)
    url: Url
    official: bool = True


class EffectiveDate(_Strict):
    """A date some part of the rule takes effect, and what takes effect then."""

    date: date
    applies_to: str = Field(min_length=2)


class Litigation(_Strict):
    """What courts have done with the rule, if anything."""

    status: Literal[
        "none_known", "challenged", "stayed", "decided", "upheld", "struck", "not_applicable",
        "unknown",
    ] = "none_known"  # fmt: skip
    note: str = ""
    citation: str | None = None
    url: Url | None = None


class ObservedResult(_Strict):
    """A measured result, with the page it was read on."""

    summary: str = Field(min_length=3)
    value: float | None = None
    unit: str | None = None
    source_label: str = Field(min_length=2)
    source_url: Url


class ComparisonRecord(_Strict):
    """One law, court decision, program or study, with its dates, status and evidence."""

    id: str = Field(pattern=ID_PATTERN)
    kind: Literal["policy", "study"]
    state: str
    jurisdiction: str = Field(min_length=2)
    policy_types: tuple[PolicyType, ...] = Field(min_length=1)
    title: str = Field(min_length=3)
    citation: str = Field(min_length=2)
    url: Url
    session: str | None = None
    bill: str | None = None
    enacted_date: date | None = None
    effective_dates: tuple[EffectiveDate, ...] = ()
    geographic_scope: str = Field(min_length=2)
    exclusions: tuple[str, ...] = ()
    implementation_status: Literal[
        "pending", "enacted", "legally_effective", "stayed", "locally_implemented",
        "practically_usable", "lapsed", "struck_down", "not_law", "unknown",
    ]  # fmt: skip
    litigation: Litigation = Litigation()
    outcome_measure: str | None = None
    denominator: str | None = None
    horizon: str | None = None
    observed_result: ObservedResult | None = None
    identification: Literal["not_applicable", "descriptive", "matched", "causal"]
    caveats: tuple[str, ...] = ()
    retrieved: date
    verified: bool
    verification_note: str = ""
    sources: tuple[SourceLink, ...] = Field(min_length=1)
    discovered_via: (
        Literal["addendum", "nyu_furman", "aei", "open_states", "pew_2026", "search"] | None
    ) = None

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        if value != "US" and value not in STATES:
            raise ValueError(f"state must be a two-letter code or US, not {value!r}")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ComparisonRecord:
        problems = []
        if not self.id.startswith(self.state.lower() + "."):
            problems.append(f"id must start with '{self.state.lower()}.'")
        if not self.verified and len(self.verification_note) < 10:
            problems.append("an unverified record needs a verification_note saying what is missing")
        if self.verified and not any(s.official for s in self.sources):
            problems.append("a verified record needs at least one official source")
        if self.kind == "study" and self.identification == "not_applicable":
            problems.append("a study must say its identification (descriptive, matched or causal)")
        if self.observed_result is not None and self.identification == "not_applicable":
            problems.append("an observed result needs an identification level")
        if (
            self.kind == "policy"
            and self.implementation_status in ("pending", "lapsed")
            and self.enacted_date is not None
        ):
            problems.append("a pending or lapsed bill cannot have an enacted date")
        if self.enacted_date and self.retrieved < self.enacted_date:
            problems.append("retrieved date is before the enacted date")
        urls = [self.url, *(s.url for s in self.sources)]
        if self.observed_result is not None:
            urls.append(self.observed_result.source_url)
        bad = [u for u in urls if any(host in u for host in NEVER_FETCH)]
        if bad and self.verified:
            problems.append("a verified record cannot rest on a code-publisher page")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def main_type(self) -> str:
        """The record's main policy type (the first listed)."""
        return self.policy_types[0]


class DiscoveryEntry(_Strict):
    """A candidate reform found in a tracker or search, not yet read in official text."""

    state: str
    policy_type: PolicyType
    label: str = Field(min_length=3)
    year: int | None = Field(default=None, ge=1950, le=2100)
    discovered_via: Literal["addendum", "nyu_furman", "aei", "open_states", "pew_2026", "search"]
    link: Url | None = None
    note: str = "unreviewed"

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        if value not in STATES:
            raise ValueError(f"state must be one of the 50 states or DC, not {value!r}")
        return value


class Tracker(_Strict):
    """A discovery tracker and what its terms allow."""

    id: Literal["nyu_furman", "aei", "open_states", "pew_2026"]
    name: str = Field(min_length=3)
    url: Url
    terms_url: Url | None = None
    programmatic_use: Literal["allowed", "not_allowed", "unclear"]
    terms_summary: str = Field(min_length=10)
    checked_on: date
