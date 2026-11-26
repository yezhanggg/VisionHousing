"""The engine's link from the law layer to the missing-middle funnel: a dated state floor.

Plain-language summary
----------------------
Scenario modules never read the law files themselves; the engine does and hands
them what they need. For missing middle in a state with a statutory floor
(Vermont's 24 V.S.A. § 4412(1)(D)), :func:`middle_floor_for` builds the
year-by-year schedule of units already allowed
(:func:`visionhousing.law.rights.middle_floor_schedule`), reads the pack's
mapped sewer share per tract (``infra_tract.sewer_share``) and names the
sources behind both. The funnel
(:class:`visionhousing.scenarios.missing_middle.MiddleFloor`) then counts only
the building sizes above what the floor already allows.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from visionhousing.ledger.loader import Ledger
from visionhousing.scenarios.missing_middle import YEAR_PARAMS, MiddleFloor

__all__ = ["middle_floor_for"]


def middle_floor_for(
    state: Any, ledger: Ledger, method: str = "mix"
) -> tuple[MiddleFloor | None, tuple[str, ...]]:
    """The dated state floor for a run of ``state`` (a ``StateContext``), and notes.

    Reads ``missing_middle_application`` in ``assumptions/law/statutory_rights.yaml``
    (:func:`visionhousing.law.rights.middle_floor_schedule`) and the pack's
    ``infra_tract`` sewer share. Returns ``(None, ())`` for a state the block
    does not list. The floor applies to the mix method only; under another
    method a note says it was not applied.
    """
    from visionhousing.law.rights import middle_floor_schedule
    from visionhousing.law.timeline import load_timeline
    from visionhousing.scenarios.extra_common import pack_table, table_sources

    code = str(getattr(state, "state", "") or "").upper()
    first = int(ledger[YEAR_PARAMS[0]].central)
    years = range(first, first + int(ledger[YEAR_PARAMS[1]].central))
    records = load_timeline()
    sched = middle_floor_schedule(code, years, records=records)
    if sched is None:
        return None, ()
    if method != "mix":
        return None, (
            f"State floor ({sched.provision}) not applied under the {method} method; "
            "it applies to the benchmark mix method only.",
        )
    tracts = state.tracts
    infra = pack_table(state, "infra_tract")
    sewer = np.zeros(len(tracts))
    notes = [
        f"State floor by date: {sched.why}. Only building sizes above what the floor already "
        "allows count, under current law in both arms (records floor_*).",
        "Where municipal water and sewer service is unknown the four-unit right is unknown; the "
        "served share is a likely range from the mapped sewer share and two draft judgement "
        "entries (no statewide municipal water map in the pack).",
    ]
    if infra is not None and "sewer_share" in infra.columns:
        share = infra.set_index("geoid")["sewer_share"]
        sewer = (
            tracts["geoid"].astype(str).map(share).astype(float).fillna(0.0).to_numpy(dtype=float)
        )
        srcs = set(table_sources(infra))
    else:
        srcs = set()
        notes.append("The pack has no sewer map (infra_tract): every lot counts as no record.")
    wanted = {*sched.versions, sched.frozen_version, sched.history_version}
    srcs |= {r.source_id for r in records if r.record_id in wanted and r.source_id}
    return MiddleFloor(sched, sewer, tuple(sorted(srcs))), tuple(notes)
