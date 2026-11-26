"""Comparisons: how reforms add up, how much reform, how robust, and with less data.

Plain-language summary
----------------------
The engine's headline answers "what does each reform add, and all five
together?". This package answers the questions that follow, always on the same
simulated futures (same seed, common random numbers):

- ``interactions``: why the package differs from the sum of the reforms alone
  (net interaction, each reform's last-in effect, an ordered allocation, the
  named mechanisms, a waterfall);
- ``dials``: each reform at 3-4 policy levels, one at a time (the benchmark
  level is the headline exactly), with "each step adds about X homes", the
  diminishing-return level, a target seeker and the ADU adoption threshold;
- ``matrix``: ADU take-up x construction cost, P50 and P10-P90 per cell;
- ``ablation``: the state run again with its local and state data hidden
  (tiers 2, 1, 0), four measures of how much worse it gets, and which missing
  dataset hurts most;
- ``ranking``: whether the order of the reforms holds when key inputs move;
- ``tod_factorial`` (opt-in): the TOD transit definition (service rule x access x
  distance) changed one factor at a time and all together, paired with the benchmark.

``comparisons(state, part=...)`` runs the parts asked for and returns one
JSON-ready dict (state and county results only). Settings live in
``assumptions/comparisons.yaml`` and ``assumptions/data_tiers.yaml``; the
method is in ``docs/methods/comparisons.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from visionhousing.engine.comparisons.ablation import data_ablation, masked_context
from visionhousing.engine.comparisons.bench import BENCHMARKS, Bench
from visionhousing.engine.comparisons.dials import adoption_threshold, policy_dials
from visionhousing.engine.comparisons.interactions import interaction_accounting
from visionhousing.engine.comparisons.matrix import matrix
from visionhousing.engine.comparisons.ranking import ranking_stability
from visionhousing.engine.comparisons.settings import load_comparisons, load_data_tiers
from visionhousing.engine.comparisons.tod_factorial import PART as TOD_FACTORIAL_PART
from visionhousing.engine.comparisons.tod_factorial import tod_factorial
from visionhousing.engine.context import StateContext
from visionhousing.ledger import DEFAULT_DRAWS

__all__ = [
    "BENCHMARKS",
    "MARKET_PART",
    "OPT_IN_PARTS",
    "PARTS",
    "TOD_FACTORIAL_PART",
    "Bench",
    "adoption_threshold",
    "comparisons",
    "data_ablation",
    "interaction_accounting",
    "masked_context",
    "matrix",
    "policy_dials",
    "ranking_stability",
    "tod_factorial",
]

PARTS = ("interactions", "dials", "matrix", "ablation", "ranking")
"""The parts ``comparisons`` runs by default (``part="all"`` in the API)."""

MARKET_PART = "market"
"""Opt-in part: the five benchmarks under the package option ``market="choice+capacity"``
(``engine.market``): today's package, project choice (the no-constraint case) and the
capacity case side by side. Asked for by name only; not in :data:`PARTS`."""

OPT_IN_PARTS = (MARKET_PART, TOD_FACTORIAL_PART)
"""Parts asked for by name only (``part="all"`` leaves them out): ``market`` and
``tod_factorial`` (``engine.comparisons.tod_factorial``)."""


def comparisons(
    state: str | StateContext,
    part: str | Iterable[str] = PARTS,
    *,
    overrides: Mapping[str, Any] | None = None,
    seed: int = 0,
    draws: int = DEFAULT_DRAWS,
    bench: Bench | None = None,
) -> dict[str, Any]:
    """Run the comparison parts asked for, on one prepared run.

    Args:
        state: State code or a loaded context.
        part: One part name, or several (default: all of :data:`PARTS`).
        overrides: The user's ledger overrides.
        seed: Seed (the same for every comparison).
        draws: Monte Carlo draws.
        bench: A bench already prepared (its seed and draws win).

    Returns:
        ``{"state", "seed", "draws", "hashes", <part>: ...}``.

    Raises:
        ValueError: Unknown part.
    """
    parts = (part,) if isinstance(part, str) else tuple(part)
    unknown = [p for p in parts if p not in (*PARTS, *OPT_IN_PARTS)]
    if unknown:
        raise ValueError(f"unknown comparison part(s) {unknown}; known: {[*PARTS, *OPT_IN_PARTS]}")
    b = bench or Bench.build(state, overrides=overrides, seed=seed, draws=draws)
    cfg = load_comparisons()
    out: dict[str, Any] = {
        "state": b.context.state,
        "seed": b.draws.seed,
        "draws": b.n,
        "synthetic": b.context.synthetic,
        "hashes": {
            "data": b.context.data_hash,
            "calibration": b.context.calibration_hash,
            "ledger": b.draws.ledger.hash(),
        },
        "county_ids": list(b.county_ids),
    }
    if "interactions" in parts:
        out["interactions"] = interaction_accounting(b, cfg)
    if "dials" in parts:
        out["dials"] = policy_dials(b, cfg)
    if "matrix" in parts:
        out["matrix"] = matrix(b, cfg)
    if "ablation" in parts:
        out["ablation"] = data_ablation(b, load_data_tiers())
    if "ranking" in parts:
        out["ranking"] = ranking_stability(b, cfg)
    if MARKET_PART in parts:
        from visionhousing.engine.core import compute  # the full run under the option

        res = compute(
            b.context,
            list(BENCHMARKS),
            dict(b.draws.overrides),
            seed=b.draws.seed,
            draws=b.n,
            market="choice+capacity",
        )
        out[MARKET_PART] = res.market.to_dict()
    if TOD_FACTORIAL_PART in parts:
        out[TOD_FACTORIAL_PART] = tod_factorial(b)
    return out
