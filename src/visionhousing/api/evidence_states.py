"""``GET /api/v1/evidence/states``: the cross-state comparison records and the completeness matrix.

Plain-language summary
----------------------
One read-only endpoint. It loads the comparison records from
``assumptions/evidence/states/`` (see :mod:`visionhousing.evidence`) and answers
with:

- ``summary``: how many records, how many verified and unverified, how many
  discovery candidates;
- ``records``: every record as stored (optionally only one state's, with
  ``?state=VT``, or only verified ones, with ``?verified=true``);
- ``matrix``: the state-by-policy-type completeness matrix in its small form
  (one letter per cell: ``R`` reviewed, ``d`` discovered but unreviewed,
  ``.`` none found) plus its counts;
- ``trackers``: each discovery tracker and what its terms allow.

The answer is rebuilt only when a record file changes. Nothing here runs the
engine, and no number in these records enters a model run.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from visionhousing.api import service
from visionhousing.evidence.matrix import build_matrix, compact, matrix_summary
from visionhousing.evidence.records import PANEL_DIR, Panel, PanelError, load_panel
from visionhousing.evidence.schema import STATES

__all__ = ["evidence_states_payload", "make_router"]

_CACHE: dict[str, tuple[tuple, Panel]] = {}


def _signature(folder) -> tuple:
    return tuple(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(folder.glob("*.yaml"))
    )


def _panel(folder=None) -> Panel:
    folder = folder if folder is not None else PANEL_DIR
    sig = _signature(folder)
    held = _CACHE.get(str(folder))
    if held is None or held[0] != sig:
        held = (sig, load_panel(folder))
        _CACHE[str(folder)] = held
    return held[1]


def evidence_states_payload(
    panel: Panel, state: str | None = None, verified: bool | None = None
) -> dict[str, Any]:
    """The endpoint's answer for a loaded panel, filtered by state and verification."""
    cells = build_matrix(panel)
    records = [
        r.model_dump(mode="json")
        for r in panel.records
        if (state is None or r.state == state) and (verified is None or r.verified == verified)
    ]
    return {
        "summary": panel.summary(),
        "records": records,
        "matrix": {"summary": matrix_summary(cells), **compact(cells)},
        "trackers": [t.model_dump(mode="json") for t in panel.trackers],
        "note": (
            "Context only: no number here enters a model run. 'none_found' means nothing was "
            "found in the sources searched so far, not that the state has no such law. "
            "'reviewed' needs a verified enacted law; 'reviewed_no_enacted_law' means only "
            "pending or lapsed bills (or other records that are not law) were read."
        ),
    }


def make_router(api: str, error: Any, folder=None) -> APIRouter:
    """Build the router; ``error(status, code, message)`` makes the app's JSON error."""
    router = APIRouter()

    @router.get(f"{api}/evidence/states")
    def evidence_states(state: str | None = None, verified: bool | None = None) -> Response:
        """Every comparison record, the completeness matrix and the trackers."""
        code = None
        if state is not None:
            code = state.upper()
            if code not in STATES and code != "US":
                return error(400, "invalid_state", "state must be a two-letter code or US")
        try:
            panel = _panel(folder)
        except PanelError as exc:
            return error(500, "evidence_invalid", str(exc)[:300])
        body = evidence_states_payload(panel, code, verified)
        return Response(service.dumps(body), media_type="application/json")

    return router
