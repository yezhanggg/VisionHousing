"""The shape of one ledger entry, and the checks every entry must pass.

A parameter is one number the model needs but cannot read from data, for example
the share of feasible lots that add an ADU each year. Each entry records a central
value, a likely low and high, the shape of the range between them, what kind of
number it is, where it comes from, and which reforms use it.

The checks here are deliberately strict. A typo in a field name, a low above the
high, or a "sourced" entry with no web address all fail when the file is loaded,
so a bad number never reaches a run.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Shape = Literal["pert", "triangular", "fixed"]
"""How values are spread between low and high when the ledger is sampled."""

Kind = Literal["sourced", "calibrated", "benchmark", "judgement"]
"""Where a number comes from: a published source, a fit to evidence, the
Challenge's own benchmark, or the team's judgement."""

Status = Literal["draft", "sourced"]
"""``sourced`` means the value has been checked against a source with a web
address. ``draft`` means it still needs that check (see ``revisit``)."""

# Every name a parameter may list in ``used_by``. A closed list catches typos.
USERS = frozenset(
    {
        "baseline",
        "adu",
        "missing_middle",
        "tod",
        "parking",
        "fees",
        "packages",
        "districts",
        "pa_hb2186",
        "pa_sb1346",
        "transit_zoning_funded_infrastructure",
        "commercial_land_housing",
        "adaptive_reuse",
        "starter_home_lots",
        "fee_deferral_with_affordability",
    }
)

PARAMETER_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
"""Parameter ids are lower case and dotted, for example ``adu.min_size_sqft``."""


class Source(BaseModel):
    """One place a number comes from.

    Attributes:
        id: Short stable name for the source, used in result records.
        citation: Plain-text reference a reader can follow.
        url: Web address of the source, if it has one.
        accessed: Date the source was read.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    citation: str = Field(min_length=1)
    url: str | None = None
    accessed: date | None = None

    @field_validator("url")
    @classmethod
    def _url_is_web_address(cls, url: str | None) -> str | None:
        if url is not None and not url.startswith(("https://", "http://")):
            raise ValueError("url must start with https:// or http://")
        return url


class Parameter(BaseModel):
    """One assumption in the ledger, with its likely range.

    Attributes:
        label: Plain-language name shown to users.
        unit: Unit of the number, for example ``$/space`` or ``share per year``.
        central: The value used when nothing is sampled.
        low: Low end of the likely range.
        high: High end of the likely range.
        shape: How draws spread between low and high.
        kind: What sort of number this is (see ``Kind``).
        source: Where the number comes from.
        rationale: One or two plain sentences on why this value.
        used_by: The reforms (or ``baseline``) that read this parameter.
        revisit: What new evidence would change it.
        status: ``sourced`` once checked against a source with a web address.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    central: float
    low: float
    high: float
    shape: Shape
    kind: Kind
    source: tuple[Source, ...] = ()
    rationale: str = Field(min_length=1)
    used_by: tuple[str, ...] = Field(min_length=1)
    revisit: str = ""
    status: Status

    @field_validator("central", "low", "high", mode="before")
    @classmethod
    def _plain_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("must be a number, not true or false")
        return value

    @field_validator("used_by")
    @classmethod
    def _known_users(cls, used_by: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(used_by) - USERS)
        if unknown:
            raise ValueError(f"unknown names {unknown}; allowed: {sorted(USERS)}")
        return used_by

    @model_validator(mode="after")
    def _range_and_labels_agree(self) -> Parameter:
        if not self.low <= self.central <= self.high:
            raise ValueError(
                f"need low <= central <= high, got {self.low}, {self.central}, {self.high}"
            )
        if self.shape == "fixed" and self.low != self.high:
            raise ValueError("a fixed parameter needs low == central == high")
        if self.shape != "fixed" and self.low == self.high:
            raise ValueError(f"shape '{self.shape}' needs low < high; use shape 'fixed'")
        if self.kind == "benchmark" and self.shape != "fixed":
            raise ValueError("a benchmark parameter is set by the Challenge; use shape 'fixed'")
        if self.status == "sourced" and not any(s.url and s.accessed for s in self.source):
            raise ValueError("status 'sourced' needs at least one source with a url and date")
        if self.status == "draft" and not self.revisit.strip():
            raise ValueError("a draft parameter needs a 'revisit' note")
        return self

    @property
    def source_ids(self) -> tuple[str, ...]:
        """Ids of this parameter's sources, in file order."""
        return tuple(s.id for s in self.source)
