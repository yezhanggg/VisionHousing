"""Policy dials: how much reform, not only reform or no reform.

Plain-language summary
----------------------
Each benchmark reform is a switch: on or off. A **dial** turns one reform's
main setting through a few levels (``assumptions/comparisons.yaml``,
``dials``): the share of lots an ADU rule reaches or an owner-occupancy rule,
the homes allowed on a single-family lot (2, 3, 4, 6), the distance from a
major stop (1/4 mile, the benchmark rule, 1/2 mile), the height near stops
(4, 5, 6, 8 storeys), the parking minimum left (today's, half, none), and the
fee cut (25, 50, 75, 100%). One dial moves at a time; every other reform stays
at its benchmark.

**Common seed.** Every level runs on the same prepared run (``bench.py``): same
seed, same baseline position in each draw, same draws of every shared input.
So the difference between two levels, draw by draw, comes from the dial alone,
and the curve is smooth. The benchmark level is exactly the headline run: its
draws are the engine's own (``tests/test_comparisons.py`` checks it).

**Stage caching.** Stages before the dial are not redone:

- data, ledger, baseline projection, shared samples and transit features are
  worked out once per run and reused by every level;
- the ADU dials act on the first stage (eligible lots), and every later stage
  of the ADU funnel scales with the lots reached, tract by tract
  (``min(feasible x r x k, feasible x r) = r x min(feasible x k, feasible)``).
  So each level is the benchmark's cached tract stages times the reach: no
  rerun at all (``cache: late_stage``);
- the other dials rerun only their own reform's funnel (``cache: reform``):
  the fee cut from its cost-to-feasibility chain on the cached baseline, the
  parking minimum from its saving step on the cached transit flags.

**Readouts, written by rule.**

- *Each step adds about X homes*: the P50, over draws, of (state added units at
  the most generous level - at the least generous level) / number of steps,
  paired draw by draw.
- *Diminishing return*: the first level after which a step adds less, per unit
  of the dial's axis, than a stated share of what the first step added per
  unit (``readouts.diminishing_step_share``).
- *Target seeker*: each curve inverted into a small table, "which level reaches
  N homes statewide" for each target N (``readouts.targets``), with "out of
  reach" where the most generous level stays below N. A level is the first
  whose P50 reaches N; between two levels the axis value is interpolated
  linearly. The same is given for "9 in 10 futures" (P10 at or above N).
- *ADU adoption threshold*: the annual take-up rate (ledger
  ``adu.takeup_feasible_annual``, its whole range times a multiplier) at which
  the five-year state P50 (and P10) of added ADUs reaches N. Worked out on the
  cached feasible stage (take-up acts only on the last two stages), always by
  bisection: the smallest multiplier whose P50 (or P10) is within the stated
  tolerance of N, the search bound doubling past ``adoption.max_multiplier``
  when needed. The ceiling is every feasible lot building one ADU, net of
  displacement (feasible x kept share); "out of reach" when even that falls
  short.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import pairwise
from typing import Any

import numpy as np

from visionhousing.engine.adapters import OPTION_LEDGER, OPTION_LOT_REACH, lot_reach
from visionhousing.engine.comparisons.bench import Bench
from visionhousing.engine.comparisons.common import (
    SPAN,
    county_table,
    homes,
    record_dict,
    share,
    state_record,
)
from visionhousing.engine.comparisons.settings import Comparisons, Dial, Level, load_comparisons

__all__ = [
    "adoption_threshold",
    "dial_curve",
    "dial_draws",
    "invert_curve",
    "policy_dials",
]

STEP_ID = "comparisons.readouts.diminishing_step_share"
TARGETS_ID = "comparisons.readouts.targets"
ADOPTION_ID = "comparisons.adoption"


def dial_draws(bench: Bench, dial: Dial, level: Level) -> tuple[np.ndarray, tuple[str, ...]]:
    """Added units by county and draw at one dial level, and the ledger ids behind them."""
    if level.is_plain:
        res = bench.alone(dial.reform)
        return bench.county(res), tuple(res.headline.assumption_ids)
    if dial.cache == "late_stage":
        res = bench.alone(dial.reform)
        reach = lot_reach(bench.context.tracts, level.options.get(OPTION_LOT_REACH))
        extra = {k: v for k, v in level.options.items() if k != OPTION_LOT_REACH}
        if extra or level.overrides or reach is None or res.level != "tract":
            raise ValueError(f"dial '{dial.key}': a late-stage level may set lot_reach only")
        index = {g: i for i, g in enumerate(bench.context.tracts["geoid"].astype(str))}
        rows = np.array([reach[index[g]] for g in res.geoids], dtype=float)
        added = np.asarray(res.added, dtype=float) * rows[:, None]
        return res.county_sum(added, bench.county_ids), tuple(res.headline.assumption_ids)
    options = dict(level.options)
    if level.overrides:
        options[OPTION_LEDGER] = dict(level.overrides)
    res = bench.alone(dial.reform, options)
    return bench.county(res), tuple(res.headline.assumption_ids)


def invert_curve(
    xs: list[float],
    values: list[float],
    keys: list[str],
    target: float,
) -> dict[str, Any]:
    """The first level whose value reaches ``target``, with the axis value interpolated.

    Args:
        xs: Axis value of each level, least to most generous.
        values: The curve's value at each level (for example the state P50).
        keys: Level ids.
        target: Homes wanted.

    Returns:
        ``status`` (``"at_lowest"``, ``"between"`` or ``"out_of_reach"``),
        ``level`` (the first level that reaches it, or ``None``) and ``x`` (the
        interpolated axis value, or ``None``).
    """
    for i, v in enumerate(values):
        if v >= target:
            if i == 0:
                return {"status": "at_lowest", "level": keys[0], "x": xs[0]}
            lo, hi = values[i - 1], v
            frac = (target - lo) / (hi - lo) if hi > lo else 1.0
            x = xs[i - 1] + frac * (xs[i] - xs[i - 1])
            return {"status": "between", "level": keys[i], "x": float(x)}
    return {"status": "out_of_reach", "level": None, "x": None}


def dial_curve(bench: Bench, dial: Dial, cfg: Comparisons) -> dict[str, Any]:
    """Every level of one dial, its steps, the readouts and the target table."""
    cids = bench.county_ids
    levels = []
    states: list[np.ndarray] = []
    all_ids: set[str] = set()
    for lv in dial.levels:
        county, ids = dial_draws(bench, dial, lv)
        all_ids |= set(ids)
        lid = f"comparisons.dials.{dial.key}.levels.{lv.key}"
        state = county.sum(axis=0)
        states.append(state)
        how = "the benchmark preset" if lv.is_plain else f"the dial at '{lv.label}'"
        expr = f"added units of the reform alone with {how}, same seed as the benchmark"
        levels.append(
            {
                "key": lv.key,
                "x": lv.x,
                "label": lv.label,
                "benchmark": lv.key == dial.benchmark,
                "options": _jsonable(lv.options),
                "overrides": _jsonable(lv.overrides),
                "record": record_dict(
                    state_record(
                        f"added units {SPAN}, {dial.title}: {lv.label}, state",
                        state,
                        expr,
                        [*ids, *dial.ids, lid],
                    )
                ),
                "counties": county_table(
                    f"added units {SPAN}, {dial.title}: {lv.label}",
                    county,
                    cids,
                    expr,
                    [*ids, *dial.ids, lid],
                ),
            }
        )
    ids = sorted({*all_ids, *dial.ids})
    steps = []
    for i in range(1, len(dial.levels)):
        a, b = dial.levels[i - 1], dial.levels[i]
        diff = states[i] - states[i - 1]
        r = state_record(
            f"step from '{a.label}' to '{b.label}', {dial.title}, state",
            diff,
            "added units at the higher level - added units at the lower level, in each draw "
            "(paired: same seed)",
            ids,
        )
        steps.append(
            {
                "from": a.key,
                "to": b.key,
                "record": record_dict(r),
                "per_unit": float(r.p50) / (b.x - a.x),
                "share_of_draws_not_lower": float(np.mean(diff >= -1e-9)),
            }
        )
    p10s = [lv["record"]["p10"] for lv in levels]
    p50s = [lv["record"]["p50"] for lv in levels]
    n_steps = max(len(dial.levels) - 1, 1)
    mean_step = float(np.median((states[-1] - states[0]) / n_steps))
    xs = [lv.x for lv in dial.levels]
    keys = [lv.key for lv in dial.levels]
    targets = [
        {
            "homes": t,
            "p50": invert_curve(xs, p50s, keys, t),
            "p10": invert_curve(xs, p10s, keys, t),
        }
        for t in cfg.targets
    ]
    return {
        "key": dial.key,
        "reform": dial.reform,
        "title": dial.title,
        "unit": dial.unit,
        "cache": dial.cache,
        "benchmark": dial.benchmark,
        "levels": levels,
        "steps": steps,
        "monotone_p50": bool(all(b >= a - 1e-9 for a, b in pairwise(p50s))),
        "each_step": {
            "homes": mean_step,
            "sentence": _step_sentence(dial, mean_step, steps),
            "formula": "P50 over draws of (state added units at the most generous level - at the "
            "least generous level) / number of steps, paired draw by draw (same seed)",
            "assumption_ids": ids,
        },
        "diminishing": _diminishing(dial, steps, cfg.diminishing_step_share, ids),
        "targets": {
            "assumption_ids": [*ids, TARGETS_ID],
            "rule": "the first level whose state P50 (or P10) reaches the target; the axis value "
            "between two levels is interpolated linearly; out of reach when the most generous "
            "level stays below it",
            "rows": targets,
        },
    }


def _jsonable(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(k): (v if isinstance(v, int | float | str | bool) else str(v)) for k, v in value.items()
    }


def _step_sentence(dial: Dial, mean_step: float, steps: list[dict[str, Any]]) -> str:
    lo = min(s["record"]["p50"] for s in steps)
    hi = max(s["record"]["p50"] for s in steps)
    first, last = dial.levels[0].label, dial.levels[-1].label
    return (
        f"{dial.title}: from {first} to {last}, each step adds about {homes(mean_step)} homes "
        f"statewide over {SPAN} (middle of the paired first-to-last difference per step; "
        f"single steps range from {homes(lo)} to {homes(hi)})."
    )


def _diminishing(
    dial: Dial, steps: list[dict[str, Any]], threshold: float, ids: list[str]
) -> dict[str, Any]:
    first = steps[0]["per_unit"]
    out: dict[str, Any] = {
        "threshold_share": threshold,
        "assumption_ids": [*ids, STEP_ID],
        "rule": "the first level after which a step adds less, per unit of the dial's axis, "
        "than the threshold share of what the first step added per unit",
        "level": None,
    }
    if first <= 0:
        out["sentence"] = (
            f"{dial.title}: the first step adds no homes, so there is no diminishing point."
        )
        return out
    for i, s in enumerate(steps[1:], start=1):
        if s["per_unit"] < threshold * first:
            lv = dial.levels[i]
            out["level"] = lv.key
            out["sentence"] = (
                f"{dial.title}: beyond {lv.label}, each further step adds less than "
                f"{share(threshold)} of what the first step added per unit of the dial."
            )
            return out
    out["sentence"] = (
        f"{dial.title}: no diminishing point inside the dial's range; every step adds at least "
        f"{share(threshold)} of what the first step added per unit."
    )
    return out


def adoption_threshold(bench: Bench, cfg: Comparisons, key: str = "adu") -> dict[str, Any]:
    """The annual ADU take-up rate needed for each target, from the cached feasible stage."""
    res = bench.alone(key)
    detail = res.detail
    if detail is None or not hasattr(detail, "late_stages"):
        raise ValueError(f"reform '{key}' has no cached late stages")
    pid = cfg.adoption_parameter
    led = res.ledger if res.ledger is not None else bench.draws.ledger
    param = led[pid]

    def state(m: float) -> np.ndarray:
        return detail.late_stages(m).sum(axis=1)

    # Ceiling: every feasible lot builds one ADU (net of displacement).
    top = (np.asarray(detail.stages["feasible"]) * detail.kept_share).sum(axis=1)
    ceiling = {q: float(np.percentile(top, q)) for q in (10, 50)}
    tol = cfg.adoption_tolerance
    rows = []
    for target in cfg.targets:
        row: dict[str, Any] = {"homes": target}
        for q in (50, 10):
            name = f"p{q}"

            def at(m: float, q: int = q) -> float:
                return float(np.percentile(state(m), q))

            hi = cfg.adoption_max_multiplier
            at_hi = at(hi)
            for _ in range(60):  # widen the bound until it reaches the target
                if ceiling[q] < target or at_hi >= target:
                    break
                hi *= 2.0
                at_hi = at(hi)
            if ceiling[q] < target or at_hi < target:
                row[name] = {"status": "out_of_reach", "multiplier": None, "rate": None}
                continue
            # Bisect until both ends sit within the tolerance of the target, so the
            # answer is the smallest multiplier that reaches it (never the bound).
            lo, at_lo = 0.0, at(0.0)
            for _ in range(200):
                if at_hi - target <= tol and target - at_lo <= tol:
                    break
                mid = (lo + hi) / 2
                value = at(mid)
                if value >= target:
                    hi, at_hi = mid, value
                else:
                    lo, at_lo = mid, value
                if hi - lo <= 1e-12 * max(hi, 1.0):
                    break
            rate = hi * param.central
            if rate < param.low:
                where = "below the ledger's range"
            elif rate > param.high:
                where = "above the ledger's range"
            else:
                where = "inside the ledger's range"
            row[name] = {
                "status": "reached",
                "multiplier": hi,
                "rate": rate,
                "rate_low": hi * param.low,
                "rate_high": hi * param.high,
                "where": where,
                "sentence": (
                    f"To reach {homes(target)} ADUs in five years "
                    f"({'in the middle future' if q == 50 else 'in 9 of 10 futures'}), about "
                    f"{share(rate)} of feasible lots would need to add an ADU each year "
                    f"({where}: {share(param.low)} to {share(param.high)})."
                ),
            }
        rows.append(row)
    bench_rec = state_record(
        f"added ADUs {SPAN} at the ledger's take-up, state",
        res.added.sum(axis=0),
        "the benchmark ADU reform alone",
        res.headline.assumption_ids,
    )
    return {
        "reform": key,
        "parameter": pid,
        "central_rate": param.central,
        "range": [param.low, param.high],
        "benchmark": record_dict(bench_rec),
        "ceiling": {
            "p10": ceiling[10],
            "p50": ceiling[50],
            "formula": "state added ADUs if every feasible lot built one: the sum over tracts "
            "of feasible lots x (1 - displacement share), P10 and P50",
        },
        "rule": "the smallest multiplier m on the whole take-up range, found by bisection (to "
        f"within {tol:g} homes of the target), so that the state P50 (or P10) of built = "
        "min(feasible x take-up x m x units per lot x ramp, feasible), net of displacement, "
        "reaches the target; rate = m x the range's central value; out of reach when even "
        "every feasible lot building one ADU falls short",
        "assumption_ids": sorted({*res.headline.assumption_ids, pid, ADOPTION_ID, TARGETS_ID}),
        "rows": rows,
    }


def policy_dials(bench: Bench, cfg: Comparisons | None = None) -> dict[str, Any]:
    """Every dial's curve, plus the ADU adoption threshold."""
    cfg = cfg or load_comparisons()
    return {
        "dials": {k: dial_curve(bench, d, cfg) for k, d in cfg.dials.items()},
        "adoption": adoption_threshold(bench, cfg),
        "seed": bench.draws.seed,
        "draws": bench.n,
    }
