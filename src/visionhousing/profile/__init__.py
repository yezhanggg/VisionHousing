"""Place profiles: plain, sourced facts about one county or tract, for market research.

Plain-language summary
----------------------
A place profile answers "what is this place like?" in short sections (people,
homes, permits, transit, data quality, and more to come). Each section opens
with one plain sentence, then lists its facts. Every fact names its source
(an id in ``src/visionhousing/data/sources.yaml``), the source's edition, the
year, the geography it is measured at and, where the source gives one, its
margin of error. A county is compared with the state; a tract with its county.

Use::

    from visionhousing.profile import build_profile, render
    profile = build_profile("PA", "42003")
    print(render(profile, "md"))

Command line: ``visionhousing profile --state PA --county 42003 --format html``.
Interface: ``GET /api/v1/profile/{state}/{geoid}``. See ``docs/modules/profile.md``.
"""

from __future__ import annotations

from visionhousing.profile.builder import build_profile, list_places
from visionhousing.profile.context import (
    MissingDataError,
    PlaceNotFoundError,
    ProfileContext,
    load_profile_context,
)
from visionhousing.profile.model import Comparison, Fact, Profile, Section
from visionhousing.profile.registry import BUILT, RESERVED, SECTIONS
from visionhousing.profile.render import FORMATS, render

__all__ = [
    "BUILT",
    "FORMATS",
    "RESERVED",
    "SECTIONS",
    "Comparison",
    "Fact",
    "MissingDataError",
    "PlaceNotFoundError",
    "Profile",
    "ProfileContext",
    "Section",
    "build_profile",
    "list_places",
    "load_profile_context",
    "render",
]
