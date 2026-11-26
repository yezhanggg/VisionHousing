"""The engine's link from the law layer to a bill's excluded places: tract-by-tract shares.

Plain-language summary
----------------------
Scenario modules never read the law files themselves; the engine does and hands
them what they need. Pennsylvania's SB 1346 leaves Pittsburgh out, but
Pittsburgh is a city inside Allegheny County. The scenario file names the place
by its Census county subdivision code (``cousub`` in the ``place_scope`` block
of ``assumptions/calibration/adu_legal_share.yaml``). :func:`measured_scope`
reads the pack's ``tract_municipalities`` table (2020 Census block assignment
file: housing units of each tract in each county subdivision), measures each
county tract's share inside the place
(:func:`visionhousing.law.places.place_tract_shares`) and returns the ADU scope
with those shares in place (``PlaceScope.by_tract``). The ADU funnel then keeps
``1 - share`` of each tract's lots, so the city's own tracts are left out and the
suburbs are not.

When the pack has no such table, the scope is returned unchanged and the flat
judged share of every county tract (the place's ``tract_share_parameter``) is
used, with a note saying so.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from visionhousing.scenarios.adu import AduScope

__all__ = ["TABLE", "measured_scope", "read_tract_municipalities"]

TABLE = "tract_municipalities"
"""Pack table holding 2020 housing units by tract and county subdivision."""

_KEY = ("pack_table", TABLE)


def read_tract_municipalities(state: Any) -> pd.DataFrame | None:
    """The pack's ``tract_municipalities`` table for a state context, or ``None``.

    Read once and kept in ``state.derived``; a table placed there under
    ``("pack_table", "tract_municipalities")`` (tests) is returned as is. The
    table's ``attrs["source_ids"]`` holds the pack dictionary's source ids.
    """
    from visionhousing.scenarios.extra_common import _dictionary_sources, _folder

    store = state.derived
    if _KEY in store:
        return store[_KEY]
    table = None
    folder: Path | None = _folder(state)
    if folder is not None:
        for cand in (folder / f"{TABLE}.csv.gz", folder / f"{TABLE}.csv"):
            if cand.is_file():
                table = pd.read_csv(cand, dtype=str)
                table.attrs["source_ids"] = _dictionary_sources(folder, TABLE)
                break
    store[_KEY] = table
    return table


def measured_scope(state: Any, scope: AduScope) -> tuple[AduScope, tuple[str, ...]]:
    """The ADU scope with each place inside a county measured tract by tract, and notes.

    Args:
        state: The ``StateContext`` (its pack folder and ``derived`` store).
        scope: The scope from :func:`visionhousing.scenarios.adu.scope_from_scenario`.

    Returns:
        ``(scope, notes)``. Places without a ``cousub`` (a whole county, such as
        Philadelphia) are unchanged. Without the pack table every place keeps
        its fallback and a note says so.
    """
    from visionhousing.law.places import place_tract_shares

    wanted = [p for p in scope.excluded if p.cousub]
    if not wanted:
        return scope, ()
    table = read_tract_municipalities(state)
    if table is None or table.empty:
        names = ", ".join(p.name for p in wanted)
        return scope, (
            f"{names}: the pack has no tract-to-municipality table, so a flat judged share of "
            "every tract in the county is left out ("
            + ", ".join(str(p.tract_share_parameter) for p in wanted)
            + ") instead of the place's own tracts.",
        )
    sources = tuple(table.attrs.get("source_ids") or ())
    places = []
    notes = []
    for place in scope.excluded:
        if not place.cousub:
            places.append(place)
            continue
        shares = place_tract_shares(table, place.county, place.cousub)
        by_tract = tuple(
            (str(t), float(s)) for t, s in zip(shares["tract"], shares["share"], strict=True)
        )
        places.append(replace(place, by_tract=by_tract, share_sources=sources))
        whole = int((shares["share"] >= 1.0).sum())
        split = int(shares["split"].sum())
        notes.append(
            f"{place.name} is left out tract by tract: {whole} of county {place.county}'s "
            f"{len(shares)} tracts lie wholly inside it and {split} are split; each tract keeps "
            "1 - (its share of 2020 housing units inside the place) of its lots (2020 Census "
            f"block assignment file, county subdivision {place.cousub}; record "
            f"place_{place.name.lower().replace(' ', '_')}_share)."
        )
    return replace(scope, excluded=tuple(places)), tuple(notes)
