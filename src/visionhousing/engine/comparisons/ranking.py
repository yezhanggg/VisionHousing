"""Ranking stability: does the order of the five reforms hold when key assumptions move?

Plain-language summary
----------------------
A reader will ask "which reform adds the most homes?". The answer is only
useful if it does not flip as soon as one uncertain input moves. This module:

1. ranks the five reforms run alone by their state P50 (the benchmark order);
2. picks the inputs that matter most: for each reform, its uncertain ledger
   inputs ranked by Spearman rank correlation with the reform's state total
   across the draws (a descriptive screen, ``sensitivity.importance``), the top
   few each (``assumptions/comparisons.yaml``, ``ranking.top_per_reform``);
3. pins each such input at the low end of its ledger range, then at the high
   end, reruns the reforms that read it (same seed), and ranks again;
4. reports, for each case, whether the order changed and which reforms moved;
5. reports, at the benchmark, which pairs of reforms have overlapping likely
   ranges (P10-P90), and in what share of the paired draws one reform adds more
   than the other.

Overlapping ranges mean the order between those two is not settled by the
model, even when the P50s differ.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np

from visionhousing.engine.comparisons.bench import BENCHMARKS, Bench
from visionhousing.engine.comparisons.common import share
from visionhousing.engine.comparisons.settings import Comparisons, load_comparisons
from visionhousing.engine.readouts import draw_share_words, mc_standard_error
from visionhousing.engine.registry import get_reform
from visionhousing.ledger import sample
from visionhousing.sensitivity.importance import importance

__all__ = ["ranking_stability", "top_inputs"]

RANKING_ID = "comparisons.ranking"


def top_inputs(bench: Bench, key: str, top: int) -> list[dict[str, Any]]:
    """The reform's uncertain inputs with the largest rank correlation to its state total."""
    res = bench.alone(key)
    led = res.ledger if res.ledger is not None else bench.draws.ledger
    ids = [i for i in res.headline.assumption_ids if i in led and led[i].shape != "fixed"]
    if not ids:
        return []
    values = sample(led, n=bench.n, seed=bench.draws.seed, ids=ids)
    frame = importance(values, np.asarray(res.added).sum(axis=0), {i: led[i].label for i in ids})
    return frame.head(top).to_dict(orient="records")


def _reads(bench: Bench, key: str, pid: str) -> bool:
    """Whether reform ``key`` reads ledger entry ``pid`` (so it must rerun when pid moves)."""
    if pid.startswith("baseline."):
        return True
    res = bench.alone(key)
    if pid in res.headline.assumption_ids:
        return True
    led = res.ledger if res.ledger is not None else bench.draws.ledger
    spec = get_reform(key)
    used = set(getattr(led[pid], "used_by", ()) or ()) if pid in led else set()
    return bool(used & {spec.family, spec.uses, key})


def _order(p50: dict[str, float]) -> list[str]:
    return sorted(p50, key=lambda k: (-p50[k], k))


def ranking_stability(
    bench: Bench,
    cfg: Comparisons | None = None,
    reforms: tuple[str, ...] = BENCHMARKS,
) -> dict[str, Any]:
    """Rankings at the benchmark and at each top input's low and high end."""
    cfg = cfg or load_comparisons()
    keys = tuple(reforms)
    base = {k: np.asarray(bench.alone_county(k)).sum(axis=0) for k in keys}
    p = {k: np.percentile(v, [10, 50, 90]) for k, v in base.items()}
    p50 = {k: float(v[1]) for k, v in p.items()}
    order = _order(p50)
    titles = {k: bench.alone(k).title for k in keys}

    chosen: dict[str, dict[str, Any]] = {}
    for k in keys:
        for row in top_inputs(bench, k, cfg.ranking_top):
            pid = str(row["parameter"])
            entry = chosen.setdefault(pid, {"parameter": pid, "label": row["label"], "for": []})
            entry["for"].append({"reform": k, "rho": float(row["rho"])})

    cases = []
    for pid in chosen:
        for end in ("low", "high"):
            moved = Bench.build(
                bench.context,
                overrides={**dict(bench.draws.overrides), pid: end},
                seed=bench.draws.seed,
                draws=bench.n,
                cycle=bench.cycle,
            )
            led = moved.draws.ledger
            vals = {}
            rerun = []
            for k in keys:
                if _reads(bench, k, pid):
                    vals[k] = float(np.percentile(moved.alone_county(k).sum(axis=0), 50))
                    rerun.append(k)
                else:
                    vals[k] = p50[k]
            new = _order(vals)
            cases.append(
                {
                    "parameter": pid,
                    "end": end,
                    "value": float(led[pid].central),
                    "p50": vals,
                    "order": new,
                    "changed": new != order,
                    "moved": [k for i, k in enumerate(new) if order[i] != k],
                    "rerun": rerun,
                }
            )
    pairs = []
    for a, b in combinations(order, 2):
        overlap = bool(p[a][0] <= p[b][2] and p[b][0] <= p[a][2])
        more = float(np.mean(base[a] > base[b]))
        n_pair = int(np.asarray(base[a]).size)
        pairs.append(
            {
                "higher": a,
                "lower": b,
                "ranges_overlap": overlap,
                "share_draws_higher_adds_more": more,
                "share_draws_higher_adds_more_se": mc_standard_error(more, n_pair),
                "text": f"{titles[a]} adds more homes than {titles[b]} "
                f"{draw_share_words(more, n_pair)}.",
            }
        )
    changed = sum(c["changed"] for c in cases)
    overlapping = [
        f"{titles[x['higher']]} and {titles[x['lower']]}" for x in pairs if x["ranges_overlap"]
    ]
    sentence = (
        f"Across {len(chosen)} key inputs, each at the low and the high end of its range "
        f"({len(cases)} cases), the order of the reforms by middle estimate changed in "
        f"{changed} case{'s' if changed != 1 else ''}."
    )
    sentence += (
        " Likely ranges overlap for: " + "; ".join(overlapping) + "."
        if overlapping
        else " No two reforms have overlapping likely ranges."
    )
    return {
        "reforms": list(keys),
        "titles": titles,
        "benchmark": {
            "order": order,
            "ranges": {
                k: {"p10": float(v[0]), "p50": float(v[1]), "p90": float(v[2])}
                for k, v in p.items()
            },
        },
        "inputs": list(chosen.values()),
        "cases": cases,
        "changed_cases": changed,
        "pairs": pairs,
        "sentence": sentence,
        "share_changed": share(changed / len(cases)) if cases else share(0),
        "rule": "inputs: each reform's top uncertain ledger inputs by |Spearman rho| with its "
        "state total; each pinned at its ledger low and high end, same seed; order by state P50",
        "assumption_ids": sorted({RANKING_ID, *chosen}),
        "seed": bench.draws.seed,
        "draws": bench.n,
    }
