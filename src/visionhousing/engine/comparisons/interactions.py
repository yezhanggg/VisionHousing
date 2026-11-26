"""Interaction accounting: why five reforms together differ from the sum of each alone.

Plain-language summary
----------------------
Each reform run alone adds some homes. Run all five together and the total is
not simply their sum: two reforms can compete for the same lots (fewer homes)
or one reform's saving can make another's projects pay (more homes). This
module measures that gap and splits it, all on **common draws** (the same seed
and the same simulated futures for every run, see ``bench.py``):

1. **Net interaction**, per draw, for the state and each county::

       package - (sum of the five reforms run alone)

2. **Last-in effect** of each reform: what it adds when it is the last one
   switched on::

       last-in(r) = package of all five - package of the other four

   Its **interaction share** is ``last-in(r) - r alone``: positive when the
   other four make reform ``r`` work better, negative when they take its lots.
   This needs five more package runs and depends on no ordering.

3. **Ordered allocation**: the reforms enter one at a time in a stated order
   (``assumptions/comparisons.yaml``, ``interactions.ordering``: the Handbook's
   numbering 1-5); each is credited with what it adds to the package built so
   far. The credits add up to the package exactly, in every draw. Another order
   gives another split; that is why the order is stated and the last-in
   effects are shown beside it.

4. **Named mechanisms**: the parts of the package rules
   (``engine.packages``), each with a sentence written by rule from its numbers:
   the fee cut making ADU and 2-6 unit projects pay, parking reform lowering
   their parking lines, ADUs and missing middle competing for lots, and parking
   with TOD near stations (measured as the package of the two minus each alone;
   zero by construction, because the TOD pro forma already has reduced parking
   and the parking reform counts baseline production only).

The waterfall payload lists the steps in order: the sum of the reforms alone,
each mechanism, and the package. Per draw the steps add up exactly; the P50s of
the steps need not add up to the P50 of the package (each is the middle of its
own draws), and the payload says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from visionhousing.engine.comparisons.bench import BENCHMARKS, Bench
from visionhousing.engine.comparisons.common import (
    SPAN,
    county_table,
    homes,
    record_dict,
    state_record,
)
from visionhousing.engine.comparisons.settings import Comparisons, load_comparisons
from visionhousing.engine.registry import get_reform

__all__ = ["interaction_accounting", "mechanism_sentence"]

ORDER_ID = "comparisons.interactions.ordering"
ZERO_ID = "comparisons.interactions.zero_homes"


def mechanism_sentence(words: str, record: Any, zero_homes: float) -> str:
    """One mechanism in a sentence, written by rule from its state record.

    Under ``zero_homes`` (absolute P50) it is "about zero"; otherwise the P50
    with its likely range, with a sign.
    """
    p10, p50, p90 = float(record.p10), float(record.p50), float(record.p90)
    if abs(p50) < zero_homes and abs(p10) < zero_homes and abs(p90) < zero_homes:
        return f"{words}: about zero homes in the package."
    sign = "+" if p50 >= 0 else "-"
    return (
        f"{words}: {sign}{homes(abs(p50))} homes in the package "
        f"(likely {homes(p10)} to {homes(p90)})."
    )


def interaction_accounting(
    bench: Bench,
    settings: Comparisons | None = None,
    reforms: tuple[str, ...] = BENCHMARKS,
) -> dict[str, Any]:
    """Net interaction, last-in effects, the ordered allocation and the mechanisms.

    Args:
        bench: The prepared run.
        settings: ``assumptions/comparisons.yaml`` (read when not given).
        reforms: The reforms in the package (default: the five benchmarks).

    Returns:
        A JSON-ready dict: ``state`` records, ``counties`` tables, ``last_in``,
        ``allocation``, ``mechanisms`` (records and sentences), ``waterfall``
        and ``check`` (the largest per-draw gap between the waterfall's steps
        and the package; 0 up to rounding).
    """
    cfg = settings or load_comparisons()
    keys = tuple(reforms)
    order = tuple(k for k in cfg.ordering if k in keys) + tuple(
        k for k in keys if k not in cfg.ordering
    )
    cids = bench.county_ids
    full = bench.package(keys)
    solo = {k: bench.alone_county(k) for k in keys}
    solo_ids = {k: set(bench.alone(k).headline.assumption_ids) for k in keys}
    all_ids = sorted({*full.ids, *(i for v in solo_ids.values() for i in v)})
    solo_sum = sum(solo.values(), np.zeros_like(full.combined))
    net = full.combined - solo_sum
    titles = {k: bench.alone(k).title for k in keys}
    names = " + ".join(titles[k] for k in keys)

    def srec(name: str, arr: np.ndarray, expr: str, ids: Any = all_ids) -> dict[str, Any]:
        return record_dict(state_record(f"{name} {SPAN}, state", arr.sum(axis=0), expr, ids))

    state = {
        "solo_sum": srec(
            "sum of the reforms run alone",
            solo_sum,
            f"the sum of each reform run alone ({names}), in each draw",
        ),
        "package": srec(
            f"combined added units, {names}",
            full.combined,
            "sum of individual effects - overlap removed + interaction added, in each draw "
            "(engine.packages)",
        ),
        "net_interaction": srec(
            "net interaction (package minus the sum of the reforms alone)",
            net,
            "package - sum of the reforms run alone, in each draw, same seed",
        ),
    }
    counties = {
        "net_interaction": county_table(
            f"net interaction {SPAN}",
            net,
            cids,
            "package - sum of the reforms run alone, in each draw",
            all_ids,
        )
    }

    last_in: dict[str, Any] = {}
    for k in keys:
        rest = bench.package(tuple(x for x in keys if x != k))
        li = full.combined - rest.combined
        share = li - solo[k]
        ids = sorted({*all_ids})
        last_in[k] = {
            "title": titles[k],
            "solo": srec(
                f"added units, {titles[k]} alone",
                solo[k],
                "the reform run alone",
                sorted(solo_ids[k]),
            ),
            "last_in": srec(
                f"last-in effect, {titles[k]}",
                li,
                f"package of all {len(keys)} - package of the other {len(keys) - 1}, "
                "in each draw, same seed",
                ids,
            ),
            "interaction_share": srec(
                f"interaction share, {titles[k]}",
                share,
                "last-in effect - the reform run alone, in each draw",
                ids,
            ),
            "counties": county_table(
                f"last-in effect {SPAN}, {titles[k]}",
                li,
                cids,
                f"package of all {len(keys)} - package of the other {len(keys) - 1}",
                ids,
            ),
        }

    allocation = []
    built = np.zeros_like(full.combined)
    for i, k in enumerate(order):
        pk = bench.package(order[: i + 1]).combined
        credit = pk - built
        built = pk
        before = " + ".join(titles[x] for x in order[:i]) or "no reform"
        allocation.append(
            {
                "reform": k,
                "title": titles[k],
                "position": i + 1,
                "record": srec(
                    f"ordered allocation, {titles[k]} (position {i + 1})",
                    credit,
                    f"package of the first {i + 1} reforms in the stated order - package of "
                    f"the first {i} ({before}), in each draw",
                    [*all_ids, ORDER_ID],
                ),
                "counties": county_table(
                    f"ordered allocation {SPAN}, {titles[k]}",
                    credit,
                    cids,
                    f"package of the first {i + 1} reforms in the stated order - package of "
                    f"the first {i}",
                    [*all_ids, ORDER_ID],
                ),
            }
        )
    alloc_gap = float(np.max(np.abs(built - full.combined))) if keys else 0.0

    mechanisms = _mechanisms(bench, cfg, full, solo, keys, all_ids)
    steps = [
        {
            "id": "solo_sum",
            "kind": "start",
            "label": "Sum of the reforms run alone",
            "record": state["solo_sum"],
        },
    ]
    per_draw = solo_sum.sum(axis=0).copy()
    for m in mechanisms:
        if m["in_package"]:
            steps.append(
                {"id": m["id"], "kind": "step", "label": m["words"], "record": m["record"]}
            )
            per_draw = per_draw + m["_draws"]
    steps.append(
        {
            "id": "package",
            "kind": "end",
            "label": "All reforms together",
            "record": state["package"],
        }
    )
    gap = float(np.max(np.abs(per_draw - full.combined.sum(axis=0)))) if keys else 0.0
    for m in mechanisms:
        m.pop("_draws", None)
    return {
        "reforms": list(keys),
        "ordering": list(order),
        "ordering_id": ORDER_ID,
        "state": state,
        "counties": counties,
        "last_in": last_in,
        "allocation": allocation,
        "mechanisms": mechanisms,
        "waterfall": {
            "steps": steps,
            "note": (
                "Per draw, the sum of the reforms alone plus each step equals the package "
                "exactly. The P50 of each step is the middle of its own draws, so the step "
                "P50s need not add up to the package's P50."
            ),
        },
        "check": {
            "waterfall_max_gap": gap,
            "allocation_max_gap": alloc_gap,
            "formula": "largest absolute per-draw difference, state, between the steps (or "
            "the ordered credits) summed and the package",
        },
        "seed": bench.draws.seed,
        "draws": bench.n,
    }


def _mechanisms(
    bench: Bench,
    cfg: Comparisons,
    full: Any,
    solo: Mapping[str, np.ndarray],
    keys: tuple[str, ...],
    ids: list[str],
) -> list[dict[str, Any]]:
    """The named mechanisms: overlap, each interaction, parking with TOD."""
    out = []

    def add(name: str, draws_c: np.ndarray, expression: str, in_package: bool) -> None:
        mid, words = cfg.mechanisms.get(name, (f"comparisons.mechanisms.{name}", name))
        record = state_record(
            f"{words.split(':')[0]} {SPAN}, state",
            draws_c.sum(axis=0),
            expression,
            [*ids, mid, ZERO_ID],
        )
        out.append(
            {
                "id": mid,
                "name": name,
                "words": words,
                "in_package": in_package,
                "record": record_dict(record),
                "sentence": mechanism_sentence(words, record, cfg.zero_homes),
                "_draws": draws_c.sum(axis=0),
            }
        )

    if np.any(full.overlap) or ("adu" in keys and "missing_middle" in keys):
        add(
            "overlap",
            -full.overlap,
            "- (missing-middle units alone - missing-middle units with the ADU lots taken out "
            "of its pool), in each draw",
            True,
        )
    for name, arr in full.interactions.items():
        add(
            name,
            arr,
            f"the reform run with the other reform's saving in its pro forma ({name}) minus "
            "the reform run before that saving, same seed, summed over counties",
            True,
        )
    fam = {get_reform(k).family: k for k in keys}
    if "parking" in fam and "tod" in fam:
        pair = bench.package((fam["parking"], fam["tod"]))
        add(
            "parking_tod",
            pair.combined - solo[fam["parking"]] - solo[fam["tod"]],
            "package of parking reform and TOD - parking alone - TOD alone, in each draw",
            False,
        )
    return out
