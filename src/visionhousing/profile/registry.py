"""The section registry: which sections a profile has, in which order, and how each is built.

Plain-language summary
----------------------
:data:`SECTIONS` lists every section key with its title, in display order.
Each key is built by the module ``visionhousing.profile.sections.<key>``, which
exposes one function::

    def build(ctx: ProfileContext, geoid: str) -> Section | None: ...

Adding a section means writing that one module; the key and title are already
here. :func:`build_section` never fails because data are missing:

- no module yet -> a "coming soon" section;
- the module returns ``None``, or raises
  :class:`~visionhousing.profile.context.MissingDataError` or ``FileNotFoundError``
  (a table or file the section needs is not there) -> a "no data for this place
  yet" section.

Any other error is a bug and is raised, so it is fixed rather than hidden.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from visionhousing.profile.context import MissingDataError, ProfileContext
from visionhousing.profile.model import Section

__all__ = [
    "BUILT",
    "RESERVED",
    "SECTIONS",
    "build_section",
    "build_sections",
    "section_keys",
    "title",
]

SECTIONS: tuple[tuple[str, str], ...] = (
    ("people", "People"),
    ("housing", "Homes"),
    ("jobs", "Jobs and employers"),
    ("permits", "Permits"),
    ("pipeline_local", "Local permit pipeline"),
    ("zoning", "Zoning and overlays"),
    ("plans", "Comprehensive plans"),
    ("incentives", "Tax credits and incentive areas"),
    ("transit", "Transit"),
    ("safety", "Safety"),
    ("owners", "Institutional buyers"),
    ("parcels", "Parcels and property"),
    ("data_quality", "Data quality"),
)
"""Every section key and its title, in display order."""

BUILT: tuple[str, ...] = ("people", "housing", "permits", "transit", "data_quality")
"""Sections built from the data already in the state pack."""

RESERVED: tuple[str, ...] = (
    "jobs",
    "incentives",
    "safety",
    "pipeline_local",
    "zoning",
    "parcels",
    "owners",
    "plans",
)
"""Keys kept for sections other modules will add."""

_TITLES = dict(SECTIONS)
PACKAGE = "visionhousing.profile.sections"


def section_keys() -> list[str]:
    """Every section key, in display order."""
    return [k for k, _ in SECTIONS]


def title(key: str) -> str:
    """A section's title (``KeyError`` for an unknown key)."""
    return _TITLES[key]


def _module(key: str):
    name = f"{PACKAGE}.{key}"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as err:
        if err.name == name:
            return None
        raise


def build_section(key: str, ctx: ProfileContext, geoid: str) -> Section:
    """Build one section, degrading to "coming soon" or "no data" instead of failing.

    Raises:
        KeyError: ``key`` is not a registered section.
        PlaceNotFoundError: The place is not in the pack (from the section's build).
    """
    name = title(key)
    module = _module(key)
    if module is None or not hasattr(module, "build"):
        return Section.coming_soon(key, name)
    try:
        section = module.build(ctx, geoid)
    except (MissingDataError, FileNotFoundError) as err:
        return Section.no_data(key, name, f"No data for this place yet: {err}.")
    if section is None:
        return Section.no_data(key, name)
    return section


def build_sections(
    ctx: ProfileContext, geoid: str, keys: Iterable[str] | None = None
) -> list[Section]:
    """Build the chosen sections (default: all) in display order."""
    wanted = section_keys() if keys is None else [k for k in section_keys() if k in set(keys)]
    return [build_section(k, ctx, geoid) for k in wanted]
