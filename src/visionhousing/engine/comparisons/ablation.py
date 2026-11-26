"""Data ablation: what if a data-rich state had only the data a sparse state has?

Plain-language summary
----------------------
The method must also run for states with less complete data. The one place
where we can measure how much worse it gets is a state where we have the full
data: run it again with some of its data hidden and compare.

**Tiers** (``assumptions/data_tiers.yaml``): tier 0 is national data only
(permits, the census survey, blocks, transit stops); tier 1 adds state-level
sources (the state's own already-legal table, state construction pay); tier 2
adds local sources (verified ordinance readings, lot measurements, network
walksheds, county pay). Running at tier ``t`` masks every input above ``t``, so
the fallback the engine already uses for a state without that input fires:

- ordinance readings masked: the local-law hooks are off; every county uses the
  statewide already-legal ADU share, and parking minimums reach every county;
- lot measurements masked: the borrowed fit range is widened, exactly as for
  any state without its own measurement (``assumptions/transfer.yaml``);
- the state's already-legal table masked: the ledger's national range is used;
- walksheds masked: straight-line distance (the benchmark already uses it);
- construction pay masked: a tract cost factor, where the pack has one, falls
  back to its county mean (tier 1), then to 1 (tier 0). The engine reads no
  cost factor where the pack has none; the run then says "no effect".

Nothing is invented: a masked input is simply absent.

**Four measures**, each tier against full data, per reform (same seed):

- *middle shift* = the P50 over draws of (ablated / full - 1), state: both runs
  use the same seed, so each draw of one is paired with the same draw of the
  other; the paired difference (ablated - full) and the per-draw ratio are
  reported with their P10 / P50 / P90 too;
- *range widening* = (P90 - P10) ablated / (P90 - P10) full, state;
- *county rank agreement* = Spearman rank correlation of the county P50s;
- *coverage* = share of counties whose full-data P50 lies inside the ablated
  P10-P90.

**Which missing dataset hurts most**: each input is masked alone and ranked by
the absolute middle shift of the package of the compared reforms (one paired,
per-draw measure on one set of draws; the reform's own when only one is
compared), then by the package's range widening. The mean shift over the
reforms is shown beside it but not ranked on. An input the engine does not
read in this state is listed with "no effect" and is not rerun.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

import numpy as np

from visionhousing.engine.comparisons.bench import BENCHMARKS, Bench
from visionhousing.engine.comparisons.common import share
from visionhousing.engine.comparisons.settings import DataTiers, load_data_tiers
from visionhousing.engine.context import (
    StateContext,
    load_transfer_rules,
    local_law_override,
    transfer_overrides,
)
from visionhousing.ledger import Ledger, apply_overrides, load_ledger
from visionhousing.scenarios.adu import legal_share_override
from visionhousing.sensitivity.importance import spearman

__all__ = [
    "PACKAGE",
    "context_at_tier",
    "data_ablation",
    "masked_context",
    "measures",
    "substantive_masks",
]

PACKAGE = "package"
"""Key of the package of all reforms in the ablation tables."""

COST_FACTOR = "cost_factor"
MEASURES_ID = "data_tiers.measures"


def _forced_transfer(state: str, ledger: Ledger) -> dict[str, dict[str, Any]]:
    """The transfer widening applied even where the state has its own measurement."""
    out: dict[str, dict[str, Any]] = {}
    known = dict(ledger.items())
    for rule in load_transfer_rules():
        pid = str(rule["parameter"])
        if pid not in known:
            continue
        change = {k: rule[k] for k in ("low", "central", "high", "shape") if k in rule}
        why = " ".join(str(rule.get("rationale", "")).split())
        change["rationale"] = (
            f"Data-ablation test: {state}'s own measurement (calibration "
            f"{rule.get('calibration', '-')}) is masked, so the range is widened as for a state "
            f"without one (assumptions/transfer.yaml). {why}"
        )
        out[pid] = change
    return out


def _effects(masks: Iterable[str], tiers: DataTiers) -> set[str]:
    unknown = [m for m in masks if m not in tiers.inputs]
    if unknown:
        raise KeyError(f"unknown data input(s) {unknown}; known: {sorted(tiers.inputs)}")
    return {tiers.inputs[m].effect for m in masks}


def masked_context(
    context: StateContext,
    masks: Iterable[str],
    tiers: DataTiers | None = None,
    *,
    base_ledger: Ledger | None = None,
) -> StateContext:
    """The context with the named data inputs hidden, so their fallbacks fire.

    Args:
        context: The full-data context.
        masks: Input ids from ``assumptions/data_tiers.yaml``.
        tiers: The tier table (read when not given).
        base_ledger: The ledger before any state calibration (default: the
            context's own base ledger; the ledger file only for a context
            built by hand without one).

    Returns:
        A new context; with no masks, the context itself.
    """
    tiers = tiers or load_data_tiers()
    masks = tuple(sorted(set(masks)))
    if not masks:
        return context
    effects = _effects(masks, tiers)
    code = context.state
    law = None if "law_hooks_off" in effects else context.local_law
    if base_ledger is not None:
        ledger = base_ledger
    elif context.base_ledger is not None:
        ledger = context.base_ledger
    else:
        ledger = load_ledger()
    legal = context.legal
    if "state_table_off" not in effects and legal.state and legal.state.upper() == code:
        ledger = apply_overrides(ledger, legal_share_override(legal))
    if "transfer_widening_on" in effects:
        ledger = apply_overrides(ledger, _forced_transfer(code, ledger))
    else:
        ledger = apply_overrides(ledger, transfer_overrides(code, ledger))
    ledger = apply_overrides(ledger, local_law_override(ledger, law))
    tracts = context.tracts
    if COST_FACTOR in tracts.columns:
        if "pay_national" in effects:
            tracts = tracts.assign(**{COST_FACTOR: 1.0})
        elif "pay_county_mean" in effects:
            mean = tracts.groupby("county")[COST_FACTOR].transform("mean")
            tracts = tracts.assign(**{COST_FACTOR: mean.to_numpy()})
    walk = None if "walksheds_off" in effects else context.transit_walk
    walk_c = None if "walksheds_off" in effects else context.transit_walk_county
    tag = "|".join(masks)

    def h(text: str) -> str:
        return hashlib.sha256(f"{text}\nmasked:{tag}".encode()).hexdigest()

    return replace(
        context,
        ledger=ledger,
        local_law=law,
        tracts=tracts,
        transit_walk=walk,
        transit_walk_county=walk_c,
        data_hash=h(context.data_hash),
        calibration_hash=h(context.calibration_hash),
    )


def context_at_tier(
    state: str | StateContext, tier: int, tiers: DataTiers | None = None
) -> StateContext:
    """A state's context with every input above ``tier`` masked (tier 2: unchanged).

    Args:
        state: State code (its pack is loaded) or a loaded context.
        tier: 0, 1 or 2.
        tiers: The tier table (read when not given).
    """
    from visionhousing.engine.context import load_context

    tiers = tiers or load_data_tiers()
    ctx = state if isinstance(state, StateContext) else load_context(str(state))
    return masked_context(ctx, tiers.masked_at(int(tier)), tiers)


def substantive_masks(
    context: StateContext, masks: Iterable[str], tiers: DataTiers | None = None
) -> list[str]:
    """The masks that change something the engine reads in this state's context."""
    tiers = tiers or load_data_tiers()
    out = []
    for m in masks:
        effect = tiers.inputs[m].effect
        law = effect == "law_hooks_off" and context.local_law is not None
        walk = effect == "walksheds_off" and context.transit_walk is not None
        pay = effect in ("pay_county_mean", "pay_national") and COST_FACTOR in context.tracts
        if law or walk or pay:
            out.append(m)
        elif effect in ("transfer_widening_on", "state_table_off"):
            masked = masked_context(context, [m], tiers)
            if masked.ledger.hash() != context.ledger.hash():
                out.append(m)
    return out


def _county_p(arr: np.ndarray) -> np.ndarray:
    return np.percentile(np.asarray(arr, dtype=float), [10, 50, 90], axis=1)


def _pct3(values: np.ndarray) -> dict[str, float] | None:
    if values.size == 0:
        return None
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return {"p10": float(p10), "p50": float(p50), "p90": float(p90)}


def measures(full: np.ndarray, ablated: np.ndarray) -> dict[str, Any]:
    """The four measures for one reform: county draws of full data and of the ablated run.

    Both runs use the same seed, so draw ``d`` of one is paired with draw ``d``
    of the other (common random numbers). The middle shift is therefore worked
    out per draw, never as a difference of two medians:

    - ``paired_difference``: P10 / P50 / P90 over draws of (ablated - full), state;
    - ``ratio``: P10 / P50 / P90 over draws of (ablated / full - 1), state, over
      the draws where the full-data total is above 0;
    - ``middle_shift``: the P50 of that per-draw ratio.

    Args:
        full: Added units by county and draw, full data.
        ablated: The same, with data masked (same seed and county order).

    Returns:
        ``middle_shift``, ``paired_difference``, ``ratio``, ``range_widening``,
        ``rank_agreement``, ``coverage`` (each ``None`` when it cannot be worked
        out, for example no draw with a full-data total above 0), plus the state ranges.
    """
    full = np.asarray(full, dtype=float)
    ablated = np.asarray(ablated, dtype=float)
    if full.shape != ablated.shape:
        raise ValueError("full and ablated draws must have the same shape (same seed and counties)")
    fs, as_ = full.sum(axis=0), ablated.sum(axis=0)
    f10, f50, f90 = np.percentile(fs, [10, 50, 90])
    a10, a50, a90 = np.percentile(as_, [10, 50, 90])
    pos = fs > 0
    rel = as_[pos] / fs[pos] - 1.0
    ratio = _pct3(rel)
    widen = float((a90 - a10) / (f90 - f10)) if f90 > f10 else None
    fc, ac = _county_p(full), _county_p(ablated)
    rho = spearman(fc[1], ac[1]) if fc.shape[1] > 1 else float("nan")
    inside = (fc[1] >= ac[0] - 1e-9) & (fc[1] <= ac[2] + 1e-9)
    return {
        "middle_shift": None if ratio is None else ratio["p50"],
        "paired_difference": _pct3(as_ - fs),
        "ratio": ratio,
        "ratio_draws": int(pos.sum()),
        "range_widening": widen,
        "rank_agreement": None if np.isnan(rho) else float(rho),
        "coverage": float(np.mean(inside)) if inside.size else None,
        "full": {"p10": float(f10), "p50": float(f50), "p90": float(f90)},
        "ablated": {"p10": float(a10), "p50": float(a50), "p90": float(a90)},
    }


def _draws(bench: Bench, keys: tuple[str, ...]) -> dict[str, np.ndarray]:
    out = {k: bench.alone_county(k) for k in keys}
    if len(keys) > 1:
        out[PACKAGE] = bench.package(keys).combined
    return out


def _sentence(title: str, tier_words: str, m: Mapping[str, Any], still: float) -> str:
    if m["middle_shift"] is None or m["range_widening"] is None:
        return f"{tier_words}, {title}: the full-data estimate is zero or has no range to compare."
    shift = m["middle_shift"]
    move = (
        "does not move"
        if abs(shift) < still
        else (f"moves {'up' if shift > 0 else 'down'} by {share(abs(shift))}")
    )
    return (
        f"{tier_words}, the {title} middle estimate {move} and the likely range is "
        f"{m['range_widening']:.2f} times as wide as with full data."
    )


def data_ablation(
    bench: Bench,
    tiers: DataTiers | None = None,
    reforms: tuple[str, ...] = BENCHMARKS,
    *,
    single: bool = True,
) -> dict[str, Any]:
    """Run the state at tiers 2, 1 and 0 (same seed) and with each input masked alone.

    Args:
        bench: The full-data (tier 2) bench.
        tiers: The tier table (read when not given).
        reforms: Reforms compared (each alone, plus their package).
        single: Also mask one input at a time.

    Returns:
        A JSON-ready dict: per tier, per reform, the state ranges and the four
        measures; the property check (a lower tier never narrower); the
        one-input ranking; sentences written by rule.
    """
    tiers = tiers or load_data_tiers()
    keys = tuple(reforms)
    ctx = bench.context
    titles = {k: bench.alone(k).title for k in keys}
    titles[PACKAGE] = "package of all reforms"
    full = _draws(bench, keys)

    def bench_for(masks: list[str]) -> Bench:
        return Bench.build(
            masked_context(ctx, masks, tiers),
            overrides=dict(bench.draws.overrides),
            seed=bench.draws.seed,
            draws=bench.n,
            cycle=bench.cycle,
        )

    words = {0: "With national data only", 1: "With national and state data only"}
    by_tier: dict[int, Any] = {}
    widths: dict[int, dict[str, float]] = {}
    for t in (2, 1, 0):
        masks = list(tiers.masked_at(t))
        real = substantive_masks(ctx, masks, tiers)
        if t == 2:
            draws = full
        elif real:
            draws = _draws(bench_for(masks), keys)
        else:
            draws = full
        rows = {}
        for k, arr in draws.items():
            m = measures(full[k], arr)
            if t < 2:
                m["sentence"] = _sentence(titles[k], words[t], m, tiers.still_shift)
            rows[k] = m
        widths[t] = {k: r["ablated"]["p90"] - r["ablated"]["p10"] for k, r in rows.items()}
        by_tier[t] = {
            "tier": t,
            "title": tiers.tiers[t],
            "masked": masks,
            "masked_with_effect": real,
            "reforms": rows,
        }
    never_narrower = {
        k: bool(widths[0][k] >= widths[1][k] - 1e-6 and widths[1][k] >= widths[2][k] - 1e-6)
        for k in full
    }
    out: dict[str, Any] = {
        "state": ctx.state,
        "titles": titles,
        "tiers": {str(t): v for t, v in by_tier.items()},
        "inputs": {
            k: {
                "tier": v.tier,
                "title": v.title,
                "effect": v.effect,
                "fallback": v.fallback,
                "sources": list(v.sources),
            }
            for k, v in tiers.inputs.items()
        },
        "property": {
            "never_narrower": never_narrower,
            "holds": all(never_narrower.values()),
            "rule": "state P90 - P10 at tier 0 >= at tier 1 >= at tier 2, per reform",
        },
        "measures_id": MEASURES_ID,
        "formulas": {
            "middle_shift": "P50 over draws of (ablated / full - 1), state, same seed (paired "
            "per draw; draws with a full-data total of 0 left out)",
            "paired_difference": "P10, P50, P90 over draws of (ablated - full), state, same seed",
            "ratio": "P10, P50, P90 over draws of (ablated / full - 1), state, same seed",
            "range_widening": "(P90 - P10) ablated / (P90 - P10) full, state",
            "rank_agreement": "Spearman rank correlation of county P50s, ablated vs full",
            "coverage": "share of counties whose full-data P50 lies inside the ablated P10-P90",
        },
        "seed": bench.draws.seed,
        "draws": bench.n,
    }
    if single:
        out["single_input"] = _single_input(ctx, tiers, keys, full, bench_for)
    return out


def _single_input(
    ctx: StateContext,
    tiers: DataTiers,
    keys: tuple[str, ...],
    full: Mapping[str, np.ndarray],
    bench_for: Any,
) -> dict[str, Any]:
    rows = []
    for key, inp in tiers.inputs.items():
        real = substantive_masks(ctx, [key], tiers)
        if not real:
            rows.append(
                {
                    "input": key,
                    "title": inp.title,
                    "tier": inp.tier,
                    "effect": "none",
                    "why": f"{ctx.state}'s data pack has no such input the engine reads, so "
                    "masking it changes nothing",
                    "score": 0.0,
                    "widening": 1.0,
                    "reforms": {},
                }
            )
            continue
        draws = _draws(bench_for([key]), keys)
        ms = {k: measures(full[k], arr) for k, arr in draws.items()}
        per = [m for k, m in ms.items() if k != PACKAGE]
        # Ranked by the package's paired per-draw shift: one measure on one set of
        # draws (the mean over reforms is shown beside it, not ranked on).
        ranked = ms[PACKAGE] if PACKAGE in ms else per[0]
        score = abs(ranked["middle_shift"] or 0.0)
        widen = ranked["range_widening"] or 1.0
        rows.append(
            {
                "input": key,
                "title": inp.title,
                "tier": inp.tier,
                "effect": inp.effect,
                "fallback": inp.fallback,
                "score": float(score),
                "widening": float(widen),
                "ranked_on": PACKAGE if PACKAGE in ms else keys[0],
                "mean_reform_shift": float(np.mean([abs(m["middle_shift"] or 0.0) for m in per])),
                "reforms": ms,
            }
        )
    rows.sort(key=lambda r: (-r["score"], -r["widening"], r["input"]))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    top = rows[0] if rows and rows[0]["score"] > 0 else None
    sentence = (
        f"Of the data a sparse state may lack, {top['title'].lower()} moves the middle estimate "
        f"of the {'package' if top['ranked_on'] == PACKAGE else top['ranked_on']} most when "
        f"missing (paired shift {share(top['score'])})."
        if top
        else "No single missing input moves the middle estimates in this state."
    )
    return {"rule": tiers.hurt_rule, "rows": rows, "sentence": sentence}
