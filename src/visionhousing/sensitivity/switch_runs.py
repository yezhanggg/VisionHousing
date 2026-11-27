"""Switch off one assumption: likely ranges, state and county, with one input fixed.

Plain-language summary
----------------------
The tornado (``sensitivity.tornado``) pins **every** input: it gives one state
total with an input at its low value and every other input at central. That
is a single number, not a likely range, and it has no county values.

:func:`switch_runs` answers the fuller question "what is the likely range if we
knew this one input exactly?". For each of a reform's top three tornado inputs
it reruns the reform three more times, with that input **fixed** at its ledger
low, central and high value, and every other input drawn exactly as in the run:

- the same seed and the same number of draws;
- each ledger parameter has its own seeded random stream, so fixing one input
  leaves every other input's draws unchanged, draw by draw (*paired draws*);
- the baseline's position in its range is the run's own, unless the input being
  fixed is that position (``engine.baseline_cycle_position``), in which case
  every draw uses the baseline multiplier at that end of the range.

Each setting keeps the county draws, so it gives a P10 / P50 / P90 record for
the state and one for every county, each with its formula, ledger ids and
sources. The fixed run at ``central`` is close to the run itself (only the one
input's spread is gone); the gap between ``low`` and ``high`` is the input's
effect with the rest of the uncertainty still in place.

Cost: three inputs x two extra settings each is six extra reform runs per
reform (the ``central`` setting is run too, so it is nine adapter calls). Results
are kept in memory and, for the default scenario, on disk next to the
comparisons parts (``api.comparison_cache``), so a server serves them again
without recomputing. :func:`cached_switch_runs` only looks; it never computes.

:func:`switch_payload` is the JSON form for the static exporter
(``<ST>/switch-ranges/<key>.json``).
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import numpy as np

from visionhousing.engine.adapters import OPTION_REPLACE
from visionhousing.engine.context import StateContext
from visionhousing.engine.core import SPAN, Result
from visionhousing.engine.montecarlo import BASELINE_STREAM
from visionhousing.engine.records import Record, format_number, from_draws
from visionhousing.engine.registry import get_reform
from visionhousing.ledger import apply_overrides

__all__ = [
    "SETTINGS",
    "TOP_SWITCH",
    "cached_switch_runs",
    "clear_switch_cache",
    "switch_payload",
    "switch_runs",
    "to_jsonable",
]

TOP_SWITCH = 3
"""How many of a reform's tornado inputs get a switch (the plan's 'top 3')."""

SETTINGS = ("low", "central", "high")
"""The three values each switched input is fixed at, from its ledger entry."""

PART_PREFIX = "switch-ranges-"
"""Disk-cache part name prefix (one part per reform key)."""

_MEMORY: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
_MEMORY_MAX = 32


def _cache_key(result: Result, key: str, top: int) -> tuple:
    h = result.hashes
    return (
        result.state,
        (h.code, h.data, h.assumptions),
        int(result.seed),
        int(result.draws),
        result.cycle,
        json.dumps(dict(result.overrides), sort_keys=True, default=str),
        json.dumps(dict(result.options.get(key, {})), sort_keys=True, default=str),
        key,
        int(top),
    )


def _default_scenario(result: Result, key: str) -> bool:
    """True when the run is the one a screen asks for (no overrides, typical cycle, no options)."""
    return not result.overrides and result.cycle == "typical" and not result.options.get(key)


def clear_switch_cache() -> None:
    """Forget every switch run kept in memory (tests)."""
    _MEMORY.clear()


def _fixed_entry(value: float) -> dict[str, Any]:
    return {"central": value, "low": value, "high": value, "shape": "fixed"}


def _one_setting(
    result: Result,
    key: str,
    parameter: str,
    label: str,
    unit: str,
    setting: str,
    value: float,
) -> dict[str, Any]:
    """Rerun one reform with ``parameter`` fixed at ``value``; summarise state and counties."""
    outcome = result.reforms[key]
    res = outcome.result
    ctx, shared = result.run_context, result.draws_obj
    if res is None or res.ledger is None or ctx is None or shared is None:
        raise ValueError("this result has no run context; rerun it with engine.run")
    ledger = res.ledger
    scale = shared.baseline_scale
    if parameter == BASELINE_STREAM:
        scale = np.full_like(np.asarray(shared.baseline_scale, dtype=float), float(value))
    else:
        ledger = apply_overrides(ledger, {parameter: _fixed_entry(float(value))})
    draws = replace(shared, baseline_scale=scale)
    opts = {**dict(outcome.options), OPTION_REPLACE: ledger}
    rerun = get_reform(key).adapter(ctx, draws, opts)
    county = rerun.county_draws(tuple(result.county_ids))
    state = county.sum(axis=0)
    head = outcome.added
    shown = format_number(float(value), unit)
    fixed = (
        f"{parameter} fixed at its {setting} value ({shown}); every other input drawn as in "
        f"the run (seed {result.seed}, {result.draws:,} draws, the same draws)"
    )
    roll = (
        "added units per tract, summed to the county in each draw"
        if rerun.level == "tract"
        else "added units per county in each draw"
    )
    common = {
        "assumption_ids": tuple(sorted({*head.assumption_ids}
                                       | ({parameter} if parameter != BASELINE_STREAM else set()))),
        "source_ids": head.source_ids,
        "tier": head.tier,
    }  # fmt: skip
    what = f"added units authorized, {outcome.title} {SPAN}, {label} at {setting}"
    state_rec = from_draws(
        f"{what}, state",
        state,
        "units",
        expression=f"the sum over counties of {roll}, with {fixed}",
        **common,
    )
    counties = {
        c: from_draws(
            f"{what}, county {c}", county[i], "units", expression=f"{roll}, with {fixed}", **common
        )
        for i, c in enumerate(result.county_ids)
    }
    return {"state": state_rec, "counties": counties}


def switch_runs(
    result: Result, key: str, top: int = TOP_SWITCH, *, use_cache: bool = True
) -> dict[str, dict[str, dict[str, Any]]]:
    """Likely ranges with each of a reform's top tornado inputs fixed at low / central / high.

    Args:
        result: An engine result (from ``engine.run``) holding the reform.
        key: Reform key in ``result.reforms``.
        top: How many tornado inputs to switch (default 3).
        use_cache: Read and keep the answer in the memory and disk caches.

    Returns:
        ``{parameter: {"low"|"central"|"high": {"state": Record, "counties":
        {county id: Record}}}}``, in tornado order (largest swing first). Each
        record is the P10 / P50 / P90 of the run's draws with that one input fixed.

    Raises:
        KeyError: The reform is not in the result.
    """
    if key not in result.reforms:
        raise KeyError(f"reform '{key}' is not in this result; it has {list(result.reforms)}")
    if use_cache:
        held = cached_switch_runs(result, key, top)
        if held is not None:
            return held
    from visionhousing.sensitivity.drivers import drivers

    frame = drivers(result, key, top=top).tornado.head(top)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in frame.itertuples(index=False):
        values = {"low": float(r.low), "central": float(r.central), "high": float(r.high)}
        out[str(r.parameter)] = {
            s: _one_setting(result, key, str(r.parameter), str(r.label), str(r.unit), s, v)
            for s, v in values.items()
        }
    if use_cache:
        _keep(result, key, top, out, frame)
    return out


# ---------------------------------------------------------------- caches


def _meta(frame: Any) -> list[dict[str, Any]]:
    return [
        {
            "rank": int(r.rank),
            "parameter": str(r.parameter),
            "label": str(r.label),
            "unit": str(r.unit),
            "inputs": {"low": float(r.low), "central": float(r.central), "high": float(r.high)},
        }
        for r in frame.itertuples(index=False)
    ]


def to_jsonable(runs: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, Any]:
    """The switch runs as plain JSON data (each record dumped)."""
    return {
        pid: {
            s: {
                "state": body["state"].model_dump(mode="json"),
                "counties": {c: r.model_dump(mode="json") for c, r in body["counties"].items()},
            }
            for s, body in by.items()
        }
        for pid, by in runs.items()
    }


def _from_jsonable(data: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        pid: {
            s: {
                "state": Record.model_validate(body["state"]),
                "counties": {c: Record.model_validate(r) for c, r in body["counties"].items()},
            }
            for s, body in by.items()
        }
        for pid, by in data.items()
    }


def _disk(result: Result):
    from visionhousing.api.comparison_cache import default_cache, fingerprint

    cache = default_cache()
    ctx = result.run_context
    if cache is None or ctx is None:
        return None, None
    return cache, fingerprint(ctx.state, seed=result.seed, draws=result.draws)


def _keep(result: Result, key: str, top: int, runs: dict, frame: Any) -> None:
    _MEMORY[_cache_key(result, key, top)] = runs
    _MEMORY.move_to_end(_cache_key(result, key, top))
    while len(_MEMORY) > _MEMORY_MAX:
        _MEMORY.popitem(last=False)
    if not _default_scenario(result, key):
        return
    cache, shared = _disk(result)
    if cache is None:
        return
    h = result.hashes
    header = {
        "state": result.state,
        "reform": key,
        "seed": result.seed,
        "draws": result.draws,
        "top": int(top),
        "hashes": {"code": h.code, "data": h.data, "assumptions": h.assumptions},
        "drivers": _meta(frame),
    }
    cache.save(result.state, shared, f"{PART_PREFIX}{key}-top{int(top)}", header,
               to_jsonable(runs))  # fmt: skip


def cached_switch_runs(
    result: Result, key: str, top: int = TOP_SWITCH
) -> dict[str, dict[str, dict[str, Any]]] | None:
    """The switch runs for ``result`` and ``key`` if already worked out (memory, then disk).

    Never computes. The disk copy is read only for the default scenario (no
    overrides, typical cycle, no reform options), and only when its key (pack,
    calibration, ledger, code, assumptions, seed, draws) matches.
    """
    held = _MEMORY.get(_cache_key(result, key, top))
    if held is not None:
        return held
    if not _default_scenario(result, key) or result.run_context is None:
        return None
    cache, shared = _disk(result)
    if cache is None:
        return None
    stored = cache.load(result.state, shared, f"{PART_PREFIX}{key}-top{int(top)}")
    if stored is None or not isinstance(stored.get("value"), dict):
        return None
    try:
        runs = _from_jsonable(stored["value"])
    except (KeyError, TypeError, ValueError):
        return None
    _MEMORY[_cache_key(result, key, top)] = runs
    return runs


# ---------------------------------------------------------------- static export


def switch_payload(
    state: str,
    key: str,
    *,
    seed: int = 0,
    draws: int | None = None,
    top: int = TOP_SWITCH,
    compute: bool = True,
    context: StateContext | None = None,
) -> dict[str, Any] | None:
    """The switch ranges of one reform alone, as JSON for ``<ST>/switch-ranges/<key>.json``.

    Runs the reform alone at the default scenario (cached by the engine), then
    reads the switch runs from the caches or, when ``compute`` is true, works
    them out.

    Args:
        state: State code.
        key: Reform key.
        seed: Seed (default 0, the screens' run).
        draws: Draws (default: the engine's 2,000).
        top: How many inputs to switch.
        compute: Work the runs out when they are not cached; when false, return
            ``None`` instead.
        context: The state's loaded context (for example the API's, read from its own
            packs folder). Without one the pack is read by state code from ``data/packs``.

    Returns:
        ``{"state", "reform", "title", "seed", "draws", "hashes", "note",
        "run": record, "drivers": [{"rank", "parameter", "label", "unit",
        "inputs": {setting: value}, "settings": {setting: {"state": record,
        "counties": {id: record}}}}]}`` or ``None``.
    """
    from visionhousing.engine.core import DEFAULT_DRAWS, run
    from visionhousing.sensitivity.drivers import drivers

    result = run(state, [key], seed=seed, draws=draws or DEFAULT_DRAWS, context=context)
    runs = cached_switch_runs(result, key, top)
    if runs is None:
        if not compute:
            return None
        runs = switch_runs(result, key, top)
    frame = drivers(result, key, top=top).tornado.head(top)
    data = to_jsonable(runs)
    rows = [{**m, "settings": data[m["parameter"]]} for m in _meta(frame) if m["parameter"] in data]
    h = result.hashes
    o = result.reforms[key]
    return {
        "state": result.state,
        "reform": key,
        "title": o.title,
        "seed": result.seed,
        "draws": result.draws,
        "hashes": {"code": h.code, "data": h.data, "assumptions": h.assumptions},
        "note": NOTE,
        "run": o.added.model_dump(mode="json"),
        "record_id": f"reform_results.{key}.added",
        "drivers": rows,
    }


NOTE = (
    "Each switch fixes one input at its low, central or high value and draws every other "
    "input as in the run (same seed, same draws), so each setting is a likely range, for "
    "the state and each county. The central setting is close to the run's own range, a "
    "little narrower because that one input no longer varies."
)
