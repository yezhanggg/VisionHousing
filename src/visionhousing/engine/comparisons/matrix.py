"""Two-variable matrix: ADU take-up against construction cost.

Plain-language summary
----------------------
Two inputs drive the ADU estimate more than most: how many owners of a lot
where an ADU fits and pays actually build one each year (take-up), and what
building costs. The matrix shows the state's added ADUs (P50 and the likely
range P10-P90) for every pair of a take-up level and a cost level
(``assumptions/comparisons.yaml``, ``matrix``).

Each level multiplies an input's whole ledger range (central, low and high),
so every draw keeps its place in the range and every cell is paired with the
benchmark draw by draw. The benchmark cell (both multipliers 1) is the
headline exactly.

**Cached late stages.** Construction cost changes which lots pay (the feasible
stage), so each cost column runs the ADU funnel once. Take-up acts only on the
last two stages (built and net), so every take-up row is worked out from the
column's cached feasible stage (``AduResult.late_stages``), with no rerun. A 5 x 5
matrix therefore costs four funnel runs, not twenty-five.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from visionhousing.engine.adapters import OPTION_LEDGER, scaled_entry
from visionhousing.engine.comparisons.bench import Bench
from visionhousing.engine.comparisons.common import SPAN, record_dict, state_record
from visionhousing.engine.comparisons.settings import Comparisons, load_comparisons

__all__ = ["matrix"]

MATRIX_ID = "comparisons.matrix"


def matrix(bench: Bench, cfg: Comparisons | None = None) -> dict[str, Any]:
    """The take-up x construction-cost matrix of state added ADUs.

    Returns:
        ``rows`` (take-up multipliers), ``columns`` (cost multipliers) and
        ``cells`` (``[row][column]``: P10 / P50 / P90 and the record).
    """
    cfg = cfg or load_comparisons()
    key = cfg.matrix_reform
    base = bench.alone(key)
    led = base.ledger if base.ledger is not None else bench.draws.ledger
    t_pid, t_mult = cfg.matrix_takeup
    c_pid, c_mult = cfg.matrix_cost
    cells: list[list[dict[str, Any]]] = [[{} for _ in c_mult] for _ in t_mult]
    for j, cm in enumerate(c_mult):
        if cm == 1.0:
            res = base
        else:
            res = bench.alone(key, {OPTION_LEDGER: {c_pid: scaled_entry(led[c_pid], cm)}})
        detail = res.detail
        for i, tm in enumerate(t_mult):
            if tm == 1.0:
                state = np.asarray(res.added, dtype=float).sum(axis=0)
            else:
                state = detail.late_stages(tm).sum(axis=1)
            r = state_record(
                f"added ADUs {SPAN}, take-up x {tm:g}, construction cost x {cm:g}, state",
                state,
                f"the ADU reform alone with {t_pid} (its whole range) x {tm:g} and {c_pid} "
                f"x {cm:g}, same seed as the benchmark",
                [*res.headline.assumption_ids, t_pid, c_pid, MATRIX_ID],
            )
            cells[i][j] = {
                "takeup": tm,
                "cost": cm,
                "p10": r.p10,
                "p50": r.p50,
                "p90": r.p90,
                "benchmark": tm == 1.0 and cm == 1.0,
                "record": record_dict(r),
            }
    return {
        "reform": key,
        "rows": {
            "parameter": t_pid,
            "label": led[t_pid].label,
            "multipliers": list(t_mult),
            "central": led[t_pid].central,
        },
        "columns": {
            "parameter": c_pid,
            "label": led[c_pid].label,
            "multipliers": list(c_mult),
            "central": led[c_pid].central,
        },
        "cells": cells,
        "funnel_runs": sum(1 for m in c_mult if m != 1.0),
        "assumption_ids": sorted({*base.headline.assumption_ids, t_pid, c_pid, MATRIX_ID}),
        "seed": bench.draws.seed,
        "draws": bench.n,
    }
