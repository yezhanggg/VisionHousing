"""The ADU budget-and-target grid: an interface address and a command.

Plain-language summary
----------------------
A housing committee asks: "with this budget, which mix of law change and ADU
program has the best chance of reaching our target, and at what net cost?".
The engine answers from a small grid worked out once on the same draws
(:func:`visionhousing.scenarios.adu_budget.budget_grid`; settings in
``assumptions/adu_budget_grid.yaml``): appropriations x award sizes x {law only,
program only, both}, with the share of draws meeting each target, the net
public cost, a Pareto flag and the out-of-range markers.

- ``GET /api/v1/states/{st}/adu-budget-grid?target=&budget=&award=&accept=&seed=&draws=``
  returns the grid, its shown text (``view``, :func:`visionhousing.api.budget_view.grid_view`)
  and, when a target is given, the answer to that question
  (:func:`visionhousing.scenarios.adu_budget.grid_lookup`): the tested options
  within the budget that meet the accepted share, the Pareto set, and flags
  (``input_missing``, ``outside_tested_range``, ``between_tested_values``,
  ``budget_exceeded``, ``no_tested_option_meets_both``). Nothing is interpolated.
- ``visionhousing adu-budget-grid --state PA`` computes the grid once and keeps it
  in the comparisons disk cache (``api.comparison_cache``, part
  ``adu_budget_grid``), so the address answers at once; ``--out`` also writes it.

The grid is read back only while the pack, calibration, ledger, code and every
assumptions file are unchanged (the cache key), so a stale grid is never served.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import Request, Response

__all__ = ["PART", "add_parser", "command", "grid_payload", "register"]

PART = "adu_budget_grid"
"""The name the grid is kept under in the comparisons disk cache."""


def _clean(value: Any) -> Any:
    from visionhousing.api.comparison_cache import _clean as clean

    return clean(value)


def grid_payload(ctx: Any, *, seed: int = 0, draws: int = 2000, compute: bool = True) -> Any:
    """The grid for a loaded state: from the disk cache, or computed and kept.

    Returns ``None`` when it is not on disk and ``compute`` is false.
    """
    from visionhousing.api.comparison_cache import default_cache, fingerprint
    from visionhousing.scenarios.adu_budget import budget_grid

    cache = default_cache()
    shared = fingerprint(ctx, seed=seed, draws=draws) if cache is not None else ""
    if cache is not None:
        hit = cache.load(ctx.state, shared, PART)
        if hit is not None:
            return hit["value"]
    if not compute:
        return None
    grid = _clean(budget_grid(ctx, seed=seed, draws=draws))
    if cache is not None:
        header = {"state": ctx.state, "seed": seed, "draws": draws}
        cache.save(ctx.state, shared, PART, header, grid)
    return grid


def _number(params: dict[str, str], name: str, error: Any) -> float | None:
    raw = params.get(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        raise error(400, "invalid_query", f"{name} must be a number") from None
    if value < 0 or value != value:
        raise error(400, "invalid_query", f"{name} must be zero or more")
    return value


def register(app: Any, *, api: str, error: Any, context: Any, timed: Any) -> None:
    """Add ``GET {api}/states/{st}/adu-budget-grid`` to ``app``."""
    from visionhousing.api.models import LIMITS
    from visionhousing.api.runner import Runner
    from visionhousing.scenarios.adu_budget import grid_lookup

    @app.get(f"{api}/states/{{st}}/adu-budget-grid")
    def adu_budget_grid(st: str, request: Request) -> Response:
        """The ADU budget-and-target grid, and the answer to one question about it."""
        if len(request.url.query) > LIMITS["query_chars"]:
            raise error(400, "query_too_long", "the address is too long")
        params = dict(request.query_params)
        try:
            seed = int(params.get("seed", 0))
            draws = int(params.get("draws", 2000))
        except ValueError:
            raise error(400, "invalid_query", "seed and draws must be whole numbers") from None
        if seed < 0 or not 1 <= draws <= LIMITS["draws"]:
            raise error(400, "invalid_query", f"seed >= 0 and 1 <= draws <= {LIMITS['draws']}")
        target = _number(params, "target", error)
        budget = _number(params, "budget", error)
        award = _number(params, "award", error)
        accept = _number(params, "accept", error)
        if accept is not None and accept > 1:
            raise error(400, "invalid_query", "accept is a share between 0 and 1")
        ctx = context(st)
        key = Runner.key(
            PART, ctx.state, ctx.data_hash, ctx.calibration_hash, ctx.ledger.hash(), seed, draws
        )
        grid = timed(key, lambda: grid_payload(ctx, seed=seed, draws=draws))
        from visionhousing.api.budget_view import grid_view

        body = {
            "grid": grid,
            "view": grid_view(grid),
            "query": grid_lookup(grid, target=target, budget=budget, award=award, accept=accept),
        }
        data = json.dumps(_clean(body), allow_nan=False, separators=(",", ":"))
        return Response(data.encode(), media_type="application/json")


def add_parser(sub: Any) -> None:
    """Add ``adu-budget-grid`` to the command line."""
    p = sub.add_parser(
        "adu-budget-grid",
        help="Work out the ADU budget-and-target grid for a state and keep it on disk",
    )
    p.add_argument("--state", default="PA", help="state code (default PA)")
    p.add_argument("--seed", type=int, default=0, help="seed (default 0)")
    p.add_argument("--draws", type=int, default=2000, help="Monte Carlo draws (default 2000)")
    p.add_argument("--out", type=Path, default=None, help="also write the grid as JSON here")


def command(args: Any) -> int:
    """Compute (or read back) the grid, print its headline cells, optionally write it."""
    from visionhousing.engine.context import load_context

    try:
        ctx = load_context(str(args.state).upper())
        grid = grid_payload(ctx, seed=args.seed, draws=args.draws)
    except (FileNotFoundError, KeyError, ValueError, RuntimeError) as err:
        print(f"adu-budget-grid: {err}", file=sys.stderr)
        return 1
    ref = grid["reference"]
    print(
        f"adu-budget-grid {grid['state']}: benchmark P50 {ref['benchmark_linear']['p50']:,.0f}, "
        f"law only (hazard) P50 {ref['law_only_hazard']['p50']:,.0f}"
    )
    for c in grid["cells"]:
        best = ", ".join(
            f"{k}: {v['share']:.0%}" for k, v in c["target_met"].items() if v["share"] > 0
        )
        print(
            f"  {c['arm']:<12} ${c['appropriation']:>12,.0f} ${c['award']:>7,.0f}  added P50 "
            f"{c['added']['p50']:>8,.0f}  net cost P50 ${c['net_public_cost']['p50']:>13,.0f}  "
            f"targets met: {best or 'none'}"
        )
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(grid, allow_nan=False, separators=(",", ":"))
        args.out.write_text(text, encoding="utf-8")
        print(f"adu-budget-grid: wrote {args.out} ({len(text):,} bytes)")
    return 0
