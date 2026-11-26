"""Build a place profile, and find places by name.

Plain-language summary
----------------------
:func:`build_profile` opens the state's pack (or uses one already open),
checks the place, builds every registered section in order and gathers the
footnotes: one entry per source a fact cites, with its name, publisher and
edition. A county's profile also carries its ranks among the state's
counties (:mod:`visionhousing.profile.ranks`). :func:`list_places` finds
counties (or tracts) whose name or code contains the search text, for the
place search box.
"""

from __future__ import annotations

from collections.abc import Iterable

from visionhousing.profile.context import (
    PlaceNotFoundError,
    ProfileContext,
    load_profile_context,
    tract_name,
)
from visionhousing.profile.model import Profile
from visionhousing.profile.ranks import county_ranks
from visionhousing.profile.registry import build_sections, section_keys

__all__ = ["PlaceNotFoundError", "build_profile", "list_places"]


def build_profile(
    state: str,
    geoid: str,
    *,
    ctx: ProfileContext | None = None,
    sections: Iterable[str] | None = None,
) -> Profile:
    """Build the profile of one county (5-digit code) or tract (11-digit code).

    Args:
        state: Two-letter state code (``"TL"`` for Testland).
        geoid: County or tract code.
        ctx: An open context for the state (opened when ``None``).
        sections: Section keys to build (default: every registered section).

    Raises:
        FileNotFoundError: No pack for the state.
        PlaceNotFoundError: The code is not a county or tract of the state.
        KeyError: An unknown section key.
    """
    ctx = ctx or load_profile_context(state)
    if ctx.state != state.upper():
        raise ValueError(f"the context is for {ctx.state}, not {state.upper()}")
    keys = None if sections is None else list(sections)
    unknown = sorted(set(keys or ()) - set(section_keys()))
    if unknown:
        raise KeyError(f"unknown profile sections: {', '.join(unknown)}")
    geoid = str(geoid).strip()
    place, _ = ctx.place(geoid)
    county = place.counties[0]
    built = build_sections(ctx, geoid, keys)
    ranks = county_ranks(ctx, geoid) if place.level == "county" else ()
    cited: list[str] = []
    for s in built:
        for src in (*s.sources, *(f.source for f in s.facts)):
            if src not in cited:
                cited.append(src)
    for r in ranks:
        for src in r.source_ids:
            if src not in cited:
                cited.append(src)
    return Profile(
        geoid=geoid,
        name=place.name if place.level == "county" else f"{place.name}, {ctx.county_name(county)}",
        level=place.level,  # type: ignore[arg-type]
        state=ctx.state,
        state_name=ctx.state_name,
        county=county,
        county_name=ctx.county_name(county),
        sections=tuple(built),
        synthetic=ctx.synthetic,
        pack_hash=ctx.pack_hash,
        sources={src: ctx.source_info(src) for src in cited},
        ranks=ranks,
    )


def list_places(
    ctx: ProfileContext, query: str = "", *, level: str = "county", limit: int = 25
) -> list[dict[str, str]]:
    """Places whose name or code contains ``query`` (case is ignored), sorted by name.

    Args:
        ctx: The state's context.
        query: Search text; empty lists every place (up to ``limit``).
        level: ``"county"`` or ``"tract"``.
        limit: Most places to return.

    Returns:
        ``[{"geoid", "name", "level", "county", "county_name"}, ...]``.

    Raises:
        ValueError: An unknown level.
    """
    if level not in ("county", "tract"):
        raise ValueError("level must be county or tract")
    q = query.strip().lower()
    counties = ctx.counties()
    names = {str(g): str(n) for g, n in zip(counties["geoid"], counties["name"], strict=True)}
    out: list[dict[str, str]] = []
    if level == "county":
        for geoid, name in sorted(names.items(), key=lambda kv: (kv[1], kv[0])):
            if not q or q in name.lower() or q in geoid:
                out.append(
                    {"geoid": geoid, "name": name, "level": "county", "county": geoid,
                     "county_name": name}
                )  # fmt: skip
    else:
        tracts = ctx.need("tracts")
        for geoid in sorted(tracts["geoid"].astype(str)):
            name = tract_name(geoid)
            county_name = names.get(geoid[:5], geoid[:5])
            full = f"{name}, {county_name}"
            if not q or q in full.lower() or q in geoid:
                out.append(
                    {"geoid": geoid, "name": full, "level": "tract", "county": geoid[:5],
                     "county_name": county_name}
                )  # fmt: skip
    return out[: max(limit, 0)]
