"""Source registry: one checked entry per data source, read from ``sources.yaml``.

Plain-language summary
----------------------
Every dataset the tool uses is listed once in ``sources.yaml`` next to this file:
who publishes it, where it is downloaded from, which year or edition ("vintage")
is used, what geography it covers, its licence and terms, roughly how big it is,
which parser function reads it, what its known limits are, and its data tier
(0 = available for every state, 1 = available for many states, 2 = local
refinement only).

This module loads that file and checks every entry against a fixed schema. A
wrong or missing field stops the load with an error that names the entry, so a
typo cannot slip through silently. Adding a source means adding one entry here
and one parser function.

The same entries feed the provenance table (see :mod:`visionhousing.data.manifest`),
so the documentation and the interface always list exactly the sources the code
fetches.
"""

from __future__ import annotations

import re
import string
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

REGISTRY_PATH = Path(__file__).with_name("sources.yaml")
ID_PATTERN = r"^[a-z][a-z0-9_]*$"
PARSER_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class RegistryError(ValueError):
    """The registry file is malformed; the message names the offending entry."""


class Access(StrEnum):
    """How a source is fetched."""

    FILE = "file"  # one direct file download
    API = "api"  # a query interface returning rows
    RANGE = "range"  # one member of a remote zip, read by HTTP range requests
    FEATURE_SERVICE = "feature_service"  # paged map-service query
    PAGE = "page"  # a web page (for example a bill's status page)


class Source(BaseModel):
    """One registry entry. Field meanings are in ``docs/modules/data_fetch.md``."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=3)
    publisher: str = Field(min_length=2)
    url_pattern: str
    vintage: str = Field(min_length=1)
    geography: tuple[str, ...] = Field(min_length=1)
    licence: str = Field(min_length=2)
    terms_url: str
    expected_size: int | None = Field(default=None, ge=0)
    parser: str = Field(pattern=PARSER_PATTERN)
    limits: str = Field(min_length=3)
    tier: int = Field(ge=0, le=2)
    access: Access = Access.FILE
    member: str | None = None
    cleaning: str = ""
    checked_on: date | None = None
    notes: str = ""

    @field_validator("url_pattern", "terms_url")
    @classmethod
    def _https(cls, value: str) -> str:
        if not re.match(r"^https://[^\s/]+\.[^\s/]+/\S*$", value):
            raise ValueError("must be an https:// URL with no spaces")
        return value

    @field_validator("geography")
    @classmethod
    def _geography_words(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not g.strip() for g in value):
            raise ValueError("geography names must not be blank")
        return value

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Names in ``{braces}`` that must be filled to make a real URL."""
        names = [f for _, f, _, _ in string.Formatter().parse(self.url_pattern) if f]
        if self.member:
            names += [f for _, f, _, _ in string.Formatter().parse(self.member) if f]
        return tuple(dict.fromkeys(names))

    def url(self, **values: Any) -> str:
        """Fill the URL pattern, for example ``url(year=2024)``; names a missing value."""
        missing = [p for p in self.placeholders if p not in values]
        if missing:
            raise RegistryError(f"source '{self.id}': url needs {', '.join(missing)}")
        return self.url_pattern.format(**values)

    def member_name(self, **values: Any) -> str | None:
        """Fill the zip-member pattern for range-read sources."""
        return self.member.format(**values) if self.member else None


def _validate_entry(entry: Any, index: int) -> Source:
    ident = entry.get("id") if isinstance(entry, dict) else None
    label = f"source '{ident}'" if ident else f"source #{index + 1} (no id)"
    if not isinstance(entry, dict):
        raise RegistryError(f"{label}: each entry must be a mapping of fields")
    try:
        source = Source.model_validate(entry)
    except ValidationError as err:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'entry'}: {e['msg']}" for e in err.errors()
        )
        raise RegistryError(f"{label}: {problems}") from None
    if source.access is Access.RANGE and not source.member:
        raise RegistryError(f"{label}: range sources must name the zip member to read")
    return source


def parse_registry(data: Any) -> dict[str, Source]:
    """Check already-loaded registry data and return sources by id."""
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise RegistryError("registry must be a mapping with a 'sources' list")
    out: dict[str, Source] = {}
    for index, entry in enumerate(data["sources"]):
        source = _validate_entry(entry, index)
        if source.id in out:
            raise RegistryError(f"source '{source.id}': duplicate id")
        out[source.id] = source
    return out


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Source]:
    """Read and check ``sources.yaml``; return the sources keyed by id, in file order."""
    with Path(path).open(encoding="utf-8") as fh:
        return parse_registry(yaml.safe_load(fh))


def get_source(source_id: str, path: Path = REGISTRY_PATH) -> Source:
    """Return one source by id, or raise :class:`RegistryError` naming it."""
    sources = load_registry(path)
    if source_id not in sources:
        raise RegistryError(f"source '{source_id}': not in the registry")
    return sources[source_id]
